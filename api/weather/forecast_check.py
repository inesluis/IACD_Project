"""Forecast check: compare the Sunday forecast with what the panels "produced".

Each day of a week (Monday to Sunday, UTC) is compared with the forecast made
on the Sunday before that week. There is no meter: "produced" is simulated as
that Sunday forecast of each hour times (1 + noise), noise ~ N(0, noise_std)
(10%, see open_meteo.yaml), never below 0.

For each installation in configs/installations.yaml: find the Sunday forecast
of the day in solar_forecast, simulate what was produced, check it, save it
in solar_actual, and draw both in a chart.

Run from the repository root, with the database up (docker compose up -d db):

    python -m api.weather.forecast_check                      # yesterday (UTC)
    python -m api.weather.forecast_check --date 2026-10-26    # a chosen day
    python -m api.weather.forecast_check --replay 2026-10-11  # a past Sunday: whole week

--replay rebuilds the forecast of a past Sunday from Open-Meteo's previous
runs (what the forecast said k days before each day), saves it in
solar_forecast with source 'replay', and checks the 7 days that follow.
"""

import argparse
import sys
import zlib
from datetime import date, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from psycopg import sql

from api.weather.db import open_database, write_rows
from api.weather.plot_tomorrow import (
    BAR_COLOR as FORECAST_COLOR,
    PLOT_DIR,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    style_hourly_axes,
)
from api.weather.solar_forecast import (
    CONFIG_DIR,
    FORECAST_COLUMNS,
    build_client,
    check_values,
    estimate_energy,
    fetch_hourly,
    float_rows,
    insert_forecast,
    load_yaml,
)

# Categorical slot 2 of the palette (slot 1, the forecast colour, comes from
# plot_tomorrow); the pair is checked for colour blindness.
PRODUCED_COLOR = "#eb6834"

# --- Plan: which day, which week --------------------------------------------


def week_sunday(day):
    """The Sunday whose forecast ``day`` is compared with.

    Weeks run Monday to Sunday; the forecast is the one made on the Sunday
    before the Monday on or before ``day``.
    """
    monday = day - timedelta(days=day.weekday())
    return monday - timedelta(days=1)


def check_day_is_over(day, today):
    """Raise ValueError unless ``day`` (UTC) is over, so its data is complete."""
    if day >= today:
        raise ValueError(f"{day} is not over yet")


def check_replay_sunday(sunday, today):
    """Raise ValueError unless ``sunday`` is a Sunday whose whole next week is over."""
    if sunday.weekday() != 6:
        raise ValueError(f"{sunday} is not a Sunday")
    last_day = sunday + timedelta(days=7)
    if last_day >= today:
        raise ValueError(f"the week ending {last_day} is not over yet")


# --- Extract: old forecasts from Open-Meteo (replay only) -------------------


def fetch_replay(client, config, installation, sunday):
    """The forecast of ``sunday`` for Monday 00:00 to Sunday 23:00, from previous runs."""
    names = [f"{name}_previous_day{k}" for name in config["hourly"] for k in range(1, 8)]
    raw = fetch_hourly(client, config["check"]["previous_runs_url"], installation, names,
                       start_date=(sunday + timedelta(days=1)).isoformat(),
                       end_date=(sunday + timedelta(days=7)).isoformat())
    return pick_lead_columns(raw, sunday, config["hourly"])


# --- Transform: "produced" = Sunday forecast x (1 + noise) ------------------


def noise_seed(user_id, day):
    """Seed for the noise of one installation and day.

    Fixed, so checking the same day again gives the same "produced" values.
    """
    return zlib.crc32(f"{user_id} {day}".encode("utf-8"))


def simulate_actual(forecast, noise_std, seed):
    """What the panels "produced": the forecast of each hour times (1 + noise).

    noise ~ N(0, noise_std), drawn independently for each hour; a factor below
    0 is cut to 0, so nothing is ever negative. gti gets the same factor as
    energy_kwh (more sun, more energy); temperature and wind are kept as forecast.
    Returns the columns of solar_actual: gti, temperature_2m, wind_speed_10m, energy_kwh.
    """
    rng = np.random.default_rng(seed)
    factor = np.clip(1 + rng.normal(0, noise_std, len(forecast)), 0, None)

    produced = forecast[[*FORECAST_COLUMNS, "energy_kwh"]].copy()
    produced["gti"] = forecast["gti"] * factor
    produced["energy_kwh"] = forecast["energy_kwh"] * factor
    return produced


# --- Transform: old forecasts, one lead per day -----------------------------


def pick_lead_columns(raw, sunday, variables):
    """Turn a Previous Runs response into the forecast made on ``sunday``.

    raw has columns "<variable>_previous_day1" to "_previous_day7" for Monday
    to Sunday. For the day sunday + k it keeps the "_previous_dayk" values:
    what the forecast said k days before. Returns one column per variable,
    with global_tilted_irradiance renamed gti.
    """
    days_ahead = (raw.index.normalize() - pd.Timestamp(sunday, tz="UTC")).days
    picked = pd.DataFrame(index=raw.index)
    for name in variables:
        picked[name] = [raw.at[time, f"{name}_previous_day{k}"]
                        for time, k in zip(raw.index, days_ahead)]
    return picked.rename(columns={"global_tilted_irradiance": "gti"})


# --- Validate: sanity checks before saving ----------------------------------


def validate_day(frame, kwp, day):
    """Raise ValueError unless ``frame`` has the 24 hours of ``day`` with sane values."""
    first = pd.Timestamp(day, tz="UTC")
    last = first + pd.Timedelta(hours=23)
    if len(frame) != 24 or frame.index[0] != first or frame.index[-1] != last:
        raise ValueError(f"expected the 24 hours of {day}, got {len(frame)}")
    check_values(frame, kwp)


def validate_replay(forecast, kwp, sunday):
    """Raise ValueError unless a replayed forecast covers Monday 00:00 to Sunday 23:00.

    Old forecasts can come back with gaps (e.g. 7 days ahead); those are
    rejected, never saved.
    """
    check_values(forecast, kwp)
    first = pd.Timestamp(sunday, tz="UTC") + pd.Timedelta(days=1)
    last = first + pd.Timedelta(days=6, hours=23)
    if len(forecast) != 7 * 24 or forecast.index[0] != first or forecast.index[-1] != last:
        raise ValueError(
            f"the replayed forecast does not cover {first:%Y-%m-%d %H:%M} to "
            f"{last:%Y-%m-%d %H:%M} UTC"
        )


# --- Load: save what was produced, find the Sunday forecast -----------------


def upsert_actual(conn, actual, user_id, model_version, source, fetched_at):
    """Insert or update the "produced" rows of one installation; return the number of rows.

    An hour already stored is overwritten, so checking the same day twice
    leaves no duplicates. All rows go in one transaction.
    """
    columns = ["user_id", "time", *FORECAST_COLUMNS, "energy_kwh",
               "model_version", "source", "fetched_at"]
    values = float_rows(actual, [*FORECAST_COLUMNS, "energy_kwh"])
    rows = [
        (user_id, time, *row, model_version, source, fetched_at)
        for time, row in zip(actual.index, values)
    ]
    return write_rows(conn, "solar_actual", columns, rows, conflict=("user_id", "time"))


def replace_replay_forecast(conn, forecast, user_id, model_version, sunday):
    """Save a replayed Sunday forecast, replacing an earlier replay of that Sunday.

    The run time is ``sunday`` 00:00 UTC and the source 'replay'. Live runs
    are never touched. Returns the number of rows saved.
    """
    run_at = pd.Timestamp(sunday, tz="UTC")
    with conn.transaction():
        conn.execute(
            "DELETE FROM solar_forecast"
            " WHERE user_id = %s AND forecast_run_at = %s AND source = 'replay'",
            (user_id, run_at),
        )
        return insert_forecast(conn, forecast, user_id, model_version, run_at, source="replay")


def sunday_forecast(conn, user_id, day, source=None):
    """Return (run_at, source, rows) of the Sunday forecast for ``day``.

    The run is the latest one made on the Sunday before the week of ``day``
    (see week_sunday). With ``source`` given, only runs of that source count;
    otherwise a live run is preferred over a replayed one. ``rows`` are the
    24 hours of ``day``, indexed by UTC time, with the weather columns,
    energy_kwh and lead_days.
    """
    columns = ["time", *FORECAST_COLUMNS, "energy_kwh", "lead_days"]
    sunday = pd.Timestamp(week_sunday(day), tz="UTC")
    monday = sunday + pd.Timedelta(days=1)
    found = conn.execute(
        "SELECT forecast_run_at, source FROM solar_forecast"
        " WHERE user_id = %s AND forecast_run_at >= %s AND forecast_run_at < %s"
        " AND (%s::text IS NULL OR source = %s)"
        " ORDER BY (source = 'live') DESC, forecast_run_at DESC LIMIT 1",
        (user_id, sunday, monday, source, source),
    ).fetchone()
    if found is None:
        raise ValueError(f"no Sunday forecast for the week of {monday.date()}")
    run_at, run_source = pd.Timestamp(found[0]).tz_convert("UTC"), found[1]

    start = pd.Timestamp(day, tz="UTC")
    query = sql.SQL(
        "SELECT {columns} FROM solar_forecast"
        " WHERE user_id = %s AND forecast_run_at = %s AND source = %s"
        " AND time >= %s AND time <= %s ORDER BY time"
    ).format(columns=sql.SQL(", ").join(map(sql.Identifier, columns)))
    rows = conn.execute(
        query, (user_id, found[0], run_source, start, start + pd.Timedelta(hours=23))
    ).fetchall()
    if len(rows) != 24:
        raise ValueError(f"the Sunday forecast covers only {len(rows)} of the 24 hours of {day}")

    frame = pd.DataFrame(rows, columns=columns)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return run_at, run_source, frame.set_index("time")


# --- Report: the line printed for each installation and day -----------------


def percent_difference(forecast_kwh, produced_kwh):
    """How far produced is from forecast, e.g. "-9%"; "n/a" with no forecast energy."""
    if forecast_kwh > 0:
        return f"{(produced_kwh - forecast_kwh) / forecast_kwh:+.0%}"
    return "n/a"


def summary_line(user_id, day, forecast_kwh, produced_kwh, chart_path):
    """One line comparing the daily totals, e.g. "... (-9%), chart <path>"."""
    return (
        f"{user_id} {day}: forecast {forecast_kwh:.1f} kWh, "
        f"produced {produced_kwh:.1f} kWh "
        f"({percent_difference(forecast_kwh, produced_kwh)}), chart {chart_path}"
    )


def plot_check(day, user_id, kwp, run_at, source, lead):
    """Draw forecast vs produced energy for one day and return the figure.

    day: 24 rows indexed by UTC time, columns forecast_kwh and produced_kwh.
    """
    date_ = day.index[0].date()
    forecast_total = day["forecast_kwh"].sum()
    produced_total = day["produced_kwh"].sum()
    hours = day.index.hour

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)

    # Dashed vs solid, so the two lines differ by more than colour.
    ax.plot(hours, day["forecast_kwh"], color=FORECAST_COLOR, linewidth=2,
            linestyle="--", marker="o", markersize=4, label="Previsão")
    ax.plot(hours, day["produced_kwh"], color=PRODUCED_COLOR, linewidth=2,
            marker="o", markersize=4, label="Produzido")
    ax.legend(loc="upper left", frameon=False, labelcolor=TEXT_PRIMARY)

    days_word = "dia" if lead == 1 else "dias"
    replay = " (replay)" if source == "replay" else ""
    fig.suptitle(
        f"Previsão vs produzido — {user_id} ({kwp} kWp) — {date_:%d/%m/%Y}",
        x=0.01, ha="left", color=TEXT_PRIMARY, fontsize=13,
    )
    # The run is always on a Sunday ("dom"): see sunday_forecast.
    ax.set_title(
        f"Previsão de dom {run_at:%d/%m}, {lead} {days_word} antes{replay}  ·  "
        f"Previsto: {forecast_total:.1f} kWh  ·  Produzido: {produced_total:.1f} kWh  ·  "
        f"Diferença: {percent_difference(forecast_total, produced_total)}",
        loc="left", color=TEXT_SECONDARY, fontsize=10,
    )
    style_hourly_axes(ax, "Hora (UTC)")
    fig.tight_layout()
    return fig


# --- Pipeline: run the stages for every installation ------------------------


def check_day(conn, config, installation, day, fetched_at, source=None):
    """Run every stage for one installation and day; return the summary line.

    source: None to prefer the live Sunday run, "replay" to use the replayed one.
    """
    user_id = installation["user_id"]
    kwp = installation["kwp"]

    run_at, run_source, forecast = sunday_forecast(conn, user_id, day, source)
    actual = simulate_actual(forecast, config["check"]["noise_std"], noise_seed(user_id, day))
    validate_day(actual, kwp, day)
    upsert_actual(conn, actual, user_id, config["model_version"],
                  config["check"]["source"], fetched_at)

    both = pd.DataFrame({
        "forecast_kwh": forecast["energy_kwh"],
        "produced_kwh": actual["energy_kwh"],
    })
    lead = int(forecast["lead_days"].iloc[0])

    fig = plot_check(both, user_id, kwp, run_at, run_source, lead)
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    path = PLOT_DIR / f"{user_id}_{day:%Y-%m-%d}_check.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)

    return summary_line(user_id, day, both["forecast_kwh"].sum(),
                        both["produced_kwh"].sum(), path)


def rebuild_replay(conn, client, config, installation, sunday):
    """Rebuild and save the forecast of a past ``sunday``; return the line to print."""
    user_id = installation["user_id"]
    weather = fetch_replay(client, config, installation, sunday)
    forecast = estimate_energy(weather, installation["kwp"], config["constants"])
    validate_replay(forecast, installation["kwp"], sunday)
    rows = replace_replay_forecast(conn, forecast, user_id, config["model_version"], sunday)
    return f"{user_id}: forecast of Sunday {sunday} rebuilt, {rows} rows saved"


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Compare the Sunday solar forecast with what the panels produced."
    )
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--date", type=date.fromisoformat, metavar="YYYY-MM-DD",
                       help="day to check, UTC (default: yesterday)")
    which.add_argument("--replay", type=date.fromisoformat, metavar="YYYY-MM-DD",
                       help="a past Sunday: rebuild its forecast and check its whole week")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()
    config = load_yaml(CONFIG_DIR / "open_meteo.yaml")
    installations = load_yaml(CONFIG_DIR / "installations.yaml")["installations"]
    today = pd.Timestamp.now(tz="UTC").date()

    # Check the dates before touching the database or the API.
    try:
        if args.replay:
            check_replay_sunday(args.replay, today)
            days = [args.replay + timedelta(days=k) for k in range(1, 8)]
        else:
            day = args.date or today - timedelta(days=1)
            check_day_is_over(day, today)
            days = [day]
    except ValueError as error:
        print(f"FAILED - {error}")
        return 1

    conn = open_database("solar_actual", [*FORECAST_COLUMNS, "energy_kwh", "source"])
    if conn is None:
        return 1

    # Only the replay asks Open-Meteo for anything.
    client = build_client() if args.replay else None
    source = "replay" if args.replay else None
    fetched_at = pd.Timestamp.now(tz="UTC")  # same timestamp for every installation

    failed = []
    for installation in installations:
        user_id = installation["user_id"]
        if args.replay:
            try:
                print(rebuild_replay(conn, client, config, installation, args.replay))
            except Exception as error:
                print(f"{user_id}: FAILED - {error}")
                failed.append(user_id)
                continue

        # One day or installation failing must not stop the others.
        for day in days:
            try:
                print(check_day(conn, config, installation, day, fetched_at, source))
            except Exception as error:
                print(f"{user_id} {day}: FAILED - {error}")
                failed.append(user_id)

    conn.close()

    # A non-zero exit code lets a scheduler notice that something failed.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

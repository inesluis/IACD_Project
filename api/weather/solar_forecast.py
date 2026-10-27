"""Solar production forecast pipeline: extract, transform, validate, load.

For each installation in configs/installations.yaml, fetch the hourly weather
forecast from Open-Meteo, estimate the energy the panels will produce each
hour, check the result and save it in the solar_forecast table (PostgreSQL).

Run from the repository root, with the database up (docker compose up -d db):

    python -m api.weather.solar_forecast
"""

import sys
from pathlib import Path

import openmeteo_requests
import pandas as pd
import requests_cache
import yaml
from dotenv import load_dotenv
from retry_requests import retry

from api.weather.db import open_database, write_rows

CONFIG_DIR = Path(__file__).parent / "configs"

# Weather columns saved with each forecast, next to energy_kwh.
FORECAST_COLUMNS = ["gti", "temperature_2m", "wind_speed_10m"]


# --- Extract: fetch the hourly weather forecast from Open-Meteo -------------


def build_client():
    """Open-Meteo client with a one-hour cache and retry on error."""
    cache_session = requests_cache.CachedSession(".cache", expire_after=3600)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    return openmeteo_requests.Client(session=retry_session)


def fetch_forecast(client, config, installation):
    """Return a DataFrame with one row per hour and one column per variable.

    The index is the time in UTC. The irradiance on the panel is in the
    column "gti" (W/m2), already adjusted for the panel's tilt and azimuth.

    Open-Meteo starts the forecast at 00:00 UTC today, so hours already past
    are included. One extra day is asked for, so the response ends at 23:00
    UTC on today + forecast_days; next_hours then drops the hours already past.
    """
    weather = fetch_hourly(client, config["url"], installation, config["hourly"],
                           forecast_days=config["forecast_days"] + 1)
    return weather.rename(columns={"global_tilted_irradiance": "gti"})


def fetch_hourly(client, url, installation, variables, **params):
    """Ask an Open-Meteo API for ``variables`` at the installation; return hourly_to_frame.

    The location, tilt and azimuth come from ``installation``; ``params`` adds
    the rest of the request (forecast_days, start_date, models, ...).
    """
    params = {
        "latitude": installation["latitude"],
        "longitude": installation["longitude"],
        "tilt": installation["tilt"],
        "azimuth": installation["azimuth"],
        "hourly": variables,
        **params,
    }
    responses = client.weather_api(url, params=params)
    return hourly_to_frame(responses[0].Hourly(), variables)


def next_hours(weather, run_at, days):
    """Keep the hours after ``run_at``: the rest of its day plus ``days`` full days.

    A run on Sunday therefore covers Monday 00:00 to the next Sunday 23:00 (UTC).
    """
    last = forecast_end(run_at, days)
    return weather[(weather.index > run_at) & (weather.index <= last)]


def forecast_end(run_at, days):
    """Last hour a run at ``run_at`` covers: 23:00 UTC, ``days`` days later."""
    return run_at.normalize() + pd.Timedelta(days=days, hours=23)


def hourly_to_frame(hourly, variables):
    """Turn the hourly block of an Open-Meteo response into a DataFrame.

    One row per hour, indexed by time in UTC, one column per variable.
    """
    time = pd.date_range(
        start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly.Interval()),
        inclusive="left",
        name="time",
    )

    # The API returns the variables in the same order they were requested.
    data = {}
    for position, name in enumerate(variables):
        data[name] = hourly.Variables(position).ValuesAsNumpy().astype("float64")

    return pd.DataFrame(data, index=time)


# --- Transform: turn hourly weather into hourly solar energy ----------------


def estimate_energy(weather, kwp, constants):
    """Return a copy of ``weather`` with a new column "energy_kwh".

    weather:   DataFrame with columns "gti" (W/m2) and "temperature_2m" (C)
    kwp:       installed peak power of the panels, in kW
    constants: dict with "noct", "gamma" and "pr" (see open_meteo.yaml)

    "gti" is the mean irradiance over the hour, so the mean power in kW over
    that hour is also the energy in kWh.
    """
    gti = weather["gti"]

    # Panels get hotter than the air when the sun shines on them.
    cell_temperature = weather["temperature_2m"] + (constants["noct"] - 20) / 800 * gti

    # Hot cells produce less: gamma is negative.
    temperature_factor = 1 + constants["gamma"] * (cell_temperature - 25)

    power_kw = kwp * (gti / 1000) * temperature_factor * constants["pr"]

    result = weather.copy()
    result["energy_kwh"] = power_kw.clip(lower=0)
    return result


# --- Validate: sanity checks on a forecast before it is saved ---------------


def validate(forecast, kwp, run_at, forecast_days):
    """Raise ValueError describing the first problem found in ``forecast``.

    The forecast must cover every hour after ``run_at``, up to 23:00 UTC
    ``forecast_days`` days later (see next_hours).
    """
    check_values(forecast, kwp)

    first = run_at.floor("h") + pd.Timedelta(hours=1)
    last = forecast_end(run_at, forecast_days)
    if forecast.index[0] != first or forecast.index[-1] != last:
        raise ValueError(
            f"the forecast does not cover {first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M} UTC"
        )


def check_values(frame, kwp):
    """Checks shared by the forecast and the forecast check.

    Hours continuous, no missing values, no negative gti, energy_kwh in range.
    """
    check_continuous(frame.index)

    for column in frame.columns:
        missing = int(frame[column].isna().sum())
        if missing > 0:
            raise ValueError(f"{missing} missing values in {column}")

    if (frame["gti"] < 0).any():
        raise ValueError("negative gti")

    # Output can pass the rated peak slightly on cold, very bright hours.
    max_energy = 1.2 * kwp
    energy = frame["energy_kwh"]
    if (energy < 0).any() or (energy > max_energy).any():
        raise ValueError(f"energy_kwh outside 0 to {max_energy:.2f} kWh")


def check_continuous(index):
    """Raise ValueError unless ``index`` goes up one hour at a time."""
    steps = index.to_series().diff().dropna()
    if not (steps == pd.Timedelta(hours=1)).all():
        raise ValueError("the hours are not continuous")


# --- Load: save a forecast in the solar_forecast table ---------------------


def lead_days(times, run_at):
    """Days between the UTC date of ``run_at`` and the UTC date of each time.

    0 for the hours of the day of the run, 1 for the next day, and so on.
    """
    return (times.normalize() - run_at.normalize()).days


def insert_forecast(conn, forecast, user_id, model_version, run_at, source="live"):
    """Insert the forecast of one installation and return the number of rows.

    All rows go in one transaction: either the whole forecast is saved or
    nothing is. Earlier runs are kept, since the run time is in the key.
    ``source`` is "live" or "replay".
    """
    columns = ["user_id", "forecast_run_at", "time", "lead_days",
               *FORECAST_COLUMNS, "energy_kwh", "model_version", "source"]
    leads = lead_days(forecast.index, run_at).tolist()
    values = float_rows(forecast, [*FORECAST_COLUMNS, "energy_kwh"])
    rows = [
        (user_id, run_at, time, lead, *row, model_version, source)
        for time, lead, row in zip(forecast.index, leads, values)
    ]
    return write_rows(conn, "solar_forecast", columns, rows)


def float_rows(frame, columns):
    """The values of ``columns``, one list of plain Python floats per row."""
    return frame[columns].astype("float64").to_numpy().tolist()


# --- Pipeline: run the stages for every installation -------------------


def load_yaml(path):
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def main():
    load_dotenv()
    config = load_yaml(CONFIG_DIR / "open_meteo.yaml")
    installations = load_yaml(CONFIG_DIR / "installations.yaml")["installations"]

    # Check the database before asking the API for anything.
    conn = open_database("solar_forecast", FORECAST_COLUMNS)
    if conn is None:
        return 1

    client = build_client()
    run_at = pd.Timestamp.now(tz="UTC")  # same timestamp for every installation

    failed = []
    for installation in installations:
        user_id = installation["user_id"]
        kwp = installation["kwp"]
        try:
            weather = fetch_forecast(client, config, installation)
            weather = next_hours(weather, run_at, config["forecast_days"])
            forecast = estimate_energy(weather, kwp, config["constants"])
            validate(forecast, kwp, run_at, config["forecast_days"])
            rows = insert_forecast(
                conn, forecast, user_id, config["model_version"], run_at
            )
            total = forecast["energy_kwh"].sum()
            print(f"{user_id}: {total:.1f} kWh forecast, {rows} rows saved to solar_forecast")
        except Exception as error:
            # One installation failing must not stop the others.
            print(f"{user_id}: FAILED - {error}")
            failed.append(user_id)

    conn.close()

    # A non-zero exit code lets a scheduler notice that something failed.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

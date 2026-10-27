"""Weather history pipeline: plan, extract, validate, load.

For each installation in configs/installations.yaml, find the days missing
from the weather_history table, fetch them from the Open-Meteo archive
(ERA5 reanalysis) and save them. The reanalysis is published about 6 days
late, so the history goes up to today minus lag_days (see open_meteo.yaml).

The first run loads everything since start_date; after that each run adds
the new day, and catches up on any day a failed or missed run left out.

Run from the repository root, with the database up (docker compose up -d db):

    python -m api.weather.weather_history

To fetch a chosen range instead, e.g. to load days before start_date or to
fetch days again, give --start and/or --end (YYYY-MM-DD, UTC days). Hours
already stored in that range are overwritten:

    python -m api.weather.weather_history --start 2024-01-01 --end 2024-12-31
"""

import argparse
import sys
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv

from api.weather.db import open_database, write_rows
from api.weather.solar_forecast import (
    CONFIG_DIR,
    build_client,
    check_continuous,
    fetch_hourly,
    float_rows,
    load_yaml,
)


# --- Plan: which days are missing -------------------------------------------


def days_to_fetch(last_time, today, config):
    """Return the (start, end) date ranges to fetch, oldest first.

    last_time: latest hour already stored for the installation, or None
    today:     today's date in UTC
    config:    the "history" section of open_meteo.yaml

    Empty when the history is up to date. Ranges are split at the end of each
    calendar year, so no single request asks for more than a year.
    """
    end = today - timedelta(days=config["lag_days"])
    if last_time is None:
        start = date.fromisoformat(config["start_date"])
    else:
        start = last_time.date() + timedelta(days=1)

    return split_by_year(start, end)


def requested_ranges(start, end, today, config):
    """Return the date ranges for a range chosen on the command line.

    start, end: the --start and --end dates, either may be None
    A missing start means config["start_date"]; a missing end means the
    last day with reanalysis (today - lag_days). Raises ValueError when the
    range is empty or goes past the last day with reanalysis.
    """
    last_available = today - timedelta(days=config["lag_days"])
    start = start or date.fromisoformat(config["start_date"])
    end = end or last_available
    if start > end:
        raise ValueError(f"--start {start} is after --end {end}")
    if end > last_available:
        raise ValueError(
            f"--end {end} is too recent: the reanalysis is only complete up to "
            f"{last_available} (today - {config['lag_days']} days)"
        )
    return split_by_year(start, end)


def split_by_year(start, end):
    """Split start..end at the end of each calendar year; empty if start > end."""
    ranges = []
    while start <= end:
        chunk_end = min(end, date(start.year, 12, 31))
        ranges.append((start, chunk_end))
        start = chunk_end + timedelta(days=1)
    return ranges


# --- Extract: fetch the reanalysis from the Open-Meteo archive --------------


def fetch_history(client, config, installation, start, end):
    """Return a DataFrame with one row per hour from ``start`` 00:00 to ``end`` 23:00 UTC.

    One column per variable in config["hourly"]. global_tilted_irradiance is
    computed by the API for the installation's tilt and azimuth.
    """
    return fetch_hourly(client, config["url"], installation, config["hourly"],
                        start_date=start.isoformat(), end_date=end.isoformat(),
                        models=config["model"])


# --- Validate: sanity checks before saving ----------------------------------


def validate_history(history, start, end):
    """Raise ValueError describing the first problem found in ``history``."""
    expected_rows = ((end - start).days + 1) * 24
    if len(history) != expected_rows:
        raise ValueError(f"expected {expected_rows} hourly rows, got {len(history)}")

    first = pd.Timestamp(start, tz="UTC")
    last = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23)
    if history.index[0] != first or history.index[-1] != last:
        raise ValueError(f"the data does not cover {start} 00:00 to {end} 23:00 UTC")

    check_continuous(history.index)

    # Missing values first, so a gap is not reported as an out-of-range value.
    for column in history.columns:
        missing = history[column].isna()
        if missing.any():
            message = f"{int(missing.sum())} missing values in {column}"
            if missing.iloc[-1]:
                message += f"; the reanalysis may not be published yet for {end}"
            raise ValueError(message)

    for column in history.columns:
        values = history[column]
        if column.startswith("cloud_cover") or column == "relative_humidity_2m":
            if ((values < 0) | (values > 100)).any():
                raise ValueError(f"{column} must be between 0 and 100")
        elif ("radiation" in column or "irradiance" in column
              or column.startswith("wind_speed") or column == "precipitation"):
            if (values < 0).any():
                raise ValueError(f"{column} must be >= 0")


# --- Load: save the history in the weather_history table --------------------


def last_history_time(conn, user_id):
    """Latest hour stored for ``user_id``, in UTC, or None if there is none."""
    (latest,) = conn.execute(
        "SELECT max(time) FROM weather_history WHERE user_id = %s", (user_id,)
    ).fetchone()
    if latest is None:
        return None
    return pd.Timestamp(latest).tz_convert("UTC")


def upsert_history(conn, history, user_id, source, fetched_at):
    """Insert or update the rows of one installation; return the number of rows.

    An hour already stored is overwritten with the new values, so fetching
    the same days twice leaves no duplicates. All rows go in one transaction.
    """
    variables = list(history.columns)
    columns = ["user_id", "time", *variables, "source", "fetched_at"]
    rows = [
        (user_id, time, *values, source, fetched_at)
        for time, values in zip(history.index, float_rows(history, variables))
    ]
    return write_rows(conn, "weather_history", columns, rows, conflict=("user_id", "time"))


# --- Pipeline: run the stages for every installation ------------------------


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Load the ERA5 weather history into the database."
    )
    parser.add_argument("--start", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="first day to fetch (default: history.start_date)")
    parser.add_argument("--end", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="last day to fetch (default: today - history.lag_days)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()
    config = load_yaml(CONFIG_DIR / "open_meteo.yaml")["history"]
    installations = load_yaml(CONFIG_DIR / "installations.yaml")["installations"]
    today = pd.Timestamp.now(tz="UTC").date()

    # A chosen range is the same for every installation; check it up front.
    chosen = None
    if args.start or args.end:
        try:
            chosen = requested_ranges(args.start, args.end, today, config)
        except ValueError as error:
            print(f"FAILED - {error}")
            return 1

    # Check the database before asking the API for anything.
    conn = open_database("weather_history", config["hourly"])
    if conn is None:
        return 1

    client = build_client()
    fetched_at = pd.Timestamp.now(tz="UTC")  # same timestamp for every installation

    failed = []
    for installation in installations:
        user_id = installation["user_id"]
        try:
            ranges = chosen
            if ranges is None:
                last_time = last_history_time(conn, user_id)
                ranges = days_to_fetch(last_time, today, config)
                if not ranges:
                    last_day = last_time.date() if last_time is not None else "none"
                    print(f"{user_id}: up to date (last day {last_day})")
                    continue

            # Oldest first: if a range fails, the next run resumes from it.
            for start, end in ranges:
                history = fetch_history(client, config, installation, start, end)
                validate_history(history, start, end)
                rows = upsert_history(conn, history, user_id, config["model"], fetched_at)
                print(f"{user_id}: {start} to {end}, {rows} rows saved")
        except Exception as error:
            # One installation failing must not stop the others.
            print(f"{user_id}: FAILED - {error}")
            failed.append(user_id)

    conn.close()

    # A non-zero exit code lets a scheduler notice that something failed.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

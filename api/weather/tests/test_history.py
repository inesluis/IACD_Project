from datetime import date

import numpy as np
import pandas as pd
import psycopg
import pytest

from api.weather.solar_forecast import CONFIG_DIR, load_yaml
from api.weather.weather_history import (
    days_to_fetch,
    requested_ranges,
    last_history_time,
    upsert_history,
    validate_history,
)

HISTORY_CONFIG = {"start_date": "2025-01-01", "lag_days": 7}
TODAY = date(2026, 10, 20)
START = date(2026, 10, 12)
END = date(2026, 10, 13)
FETCHED_AT = pd.Timestamp("2026-10-20 08:00", tz="UTC")


@pytest.fixture
def history():
    """Two days of plausible hourly reanalysis, shaped like fetch_history's output."""
    variables = load_yaml(CONFIG_DIR / "open_meteo.yaml")["history"]["hourly"]
    time = pd.date_range(
        "2026-10-12", periods=48, freq=pd.Timedelta(hours=1), tz="UTC", name="time"
    )
    # Zero at night, up to 600 W/m2 at midday.
    sun = np.clip(np.sin((time.hour - 6) / 12 * np.pi), 0, None) * 600

    data = {}
    for name in variables:
        if "radiation" in name or "irradiance" in name:
            data[name] = sun
        elif name.startswith("cloud_cover"):
            data[name] = 40.0
        elif name == "relative_humidity_2m":
            data[name] = 70.0
        elif name.startswith("wind_speed"):
            data[name] = 10.0
        elif name == "precipitation":
            data[name] = 0.0
        else:  # temperature_2m
            data[name] = 16.0
    return pd.DataFrame(data, index=time)


# --- days_to_fetch -----------------------------------------------------------


def test_no_rows_starts_at_start_date_and_splits_by_year():
    assert days_to_fetch(None, TODAY, HISTORY_CONFIG) == [
        (date(2025, 1, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 10, 13)),
    ]


def test_up_to_date_history_needs_nothing():
    last = pd.Timestamp("2026-10-13 23:00", tz="UTC")

    assert days_to_fetch(last, TODAY, HISTORY_CONFIG) == []


def test_missed_runs_fetch_only_the_missing_days():
    last = pd.Timestamp("2026-10-10 23:00", tz="UTC")

    assert days_to_fetch(last, TODAY, HISTORY_CONFIG) == [
        (date(2026, 10, 11), date(2026, 10, 13))
    ]


# --- requested_ranges (--start / --end) --------------------------------------


def test_chosen_range_is_fetched_whole_and_split_by_year():
    assert requested_ranges(date(2024, 6, 1), date(2025, 2, 28), TODAY, HISTORY_CONFIG) == [
        (date(2024, 6, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 2, 28)),
    ]


def test_missing_end_goes_up_to_the_last_day_with_reanalysis():
    assert requested_ranges(date(2026, 10, 1), None, TODAY, HISTORY_CONFIG) == [
        (date(2026, 10, 1), date(2026, 10, 13))
    ]


def test_missing_start_begins_at_start_date():
    assert requested_ranges(None, date(2025, 1, 31), TODAY, HISTORY_CONFIG) == [
        (date(2025, 1, 1), date(2025, 1, 31))
    ]


def test_start_after_end_is_rejected():
    with pytest.raises(ValueError, match="--start 2025-03-01 is after --end 2025-02-01"):
        requested_ranges(date(2025, 3, 1), date(2025, 2, 1), TODAY, HISTORY_CONFIG)


def test_end_after_the_last_day_with_reanalysis_is_rejected():
    with pytest.raises(ValueError, match="only complete up to 2026-10-13"):
        requested_ranges(date(2026, 10, 1), date(2026, 10, 15), TODAY, HISTORY_CONFIG)


# --- validate_history --------------------------------------------------------


def test_good_history_passes(history):
    validate_history(history, START, END)


def test_missing_row_is_rejected(history):
    with pytest.raises(ValueError, match="expected 48 hourly rows, got 47"):
        validate_history(history.iloc[:-1], START, END)


def test_range_that_starts_late_is_rejected(history):
    history.index = history.index + pd.Timedelta(hours=1)

    with pytest.raises(ValueError, match="does not cover 2026-10-12 00:00"):
        validate_history(history, START, END)


def test_trailing_missing_values_say_the_reanalysis_is_not_published(history):
    history.iloc[-3:, history.columns.get_loc("shortwave_radiation")] = np.nan

    with pytest.raises(ValueError, match="may not be published yet"):
        validate_history(history, START, END)


def test_negative_radiation_is_rejected(history):
    history["direct_radiation"] = -1.0

    with pytest.raises(ValueError, match="direct_radiation must be >= 0"):
        validate_history(history, START, END)


def test_cloud_cover_above_100_is_rejected(history):
    history["cloud_cover_low"] = 101.0

    with pytest.raises(ValueError, match="cloud_cover_low must be between 0 and 100"):
        validate_history(history, START, END)


def test_negative_wind_speed_is_rejected(history):
    history["wind_speed_80m"] = -0.5

    with pytest.raises(ValueError, match="wind_speed_80m must be >= 0"):
        validate_history(history, START, END)


# --- database ----------------------------------------------------------------


def test_upsert_twice_leaves_no_duplicates_and_newer_values_win(db_conn, history):
    upsert_history(db_conn, history, "demo", "era5_seamless", FETCHED_AT)
    newer = history.copy()
    newer["temperature_2m"] = 99.0
    later = FETCHED_AT + pd.Timedelta(days=1)
    upsert_history(db_conn, newer, "demo", "era5_seamless", later)

    count, lowest = db_conn.execute(
        "SELECT count(*), min(temperature_2m) FROM weather_history WHERE user_id = 'demo'"
    ).fetchone()
    assert count == 48
    assert lowest == 99.0


def test_last_history_time_per_installation(db_conn, history):
    assert last_history_time(db_conn, "demo") is None

    upsert_history(db_conn, history, "demo", "era5_seamless", FETCHED_AT)

    assert last_history_time(db_conn, "demo") == pd.Timestamp("2026-10-13 23:00", tz="UTC")
    assert last_history_time(db_conn, "other") is None


def test_out_of_range_row_is_rejected_by_the_database(db_conn, history):
    history.iloc[0, history.columns.get_loc("cloud_cover")] = 150.0

    with pytest.raises(psycopg.errors.CheckViolation):
        upsert_history(db_conn, history, "demo", "era5_seamless", FETCHED_AT)

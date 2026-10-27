import pandas as pd
import pytest

from api.weather.solar_forecast import next_hours


@pytest.fixture
def eight_days():
    """What Open-Meteo returns for forecast_days=8 when run on Sunday 25 Oct."""
    time = pd.date_range(
        "2026-10-25", periods=8 * 24, freq=pd.Timedelta(hours=1), tz="UTC", name="time"
    )
    return pd.DataFrame({"gti": 0.0}, index=time)


def test_sunday_evening_run_covers_until_next_sunday_2300(eight_days):
    run_at = pd.Timestamp("2026-10-25 18:20", tz="UTC")

    kept = next_hours(eight_days, run_at, 7)

    assert kept.index[0] == pd.Timestamp("2026-10-25 19:00", tz="UTC")
    assert kept.index[-1] == pd.Timestamp("2026-11-01 23:00", tz="UTC")
    assert len(kept) == 173


def test_run_late_on_sunday_starts_on_monday(eight_days):
    run_at = pd.Timestamp("2026-10-25 23:30", tz="UTC")

    kept = next_hours(eight_days, run_at, 7)

    assert kept.index[0] == pd.Timestamp("2026-10-26 00:00", tz="UTC")
    assert kept.index[-1] == pd.Timestamp("2026-11-01 23:00", tz="UTC")
    assert len(kept) == 168

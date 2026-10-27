import pandas as pd
import psycopg
import pytest

from api.weather.solar_forecast import estimate_energy, insert_forecast, lead_days

from api.weather.tests.conftest import CONSTANTS, KWP

RUN_AT = pd.Timestamp("2026-06-01 06:30", tz="UTC")


@pytest.fixture
def forecast(weather):
    return estimate_energy(weather, KWP, CONSTANTS)


def test_lead_days_is_zero_today_and_one_tomorrow():
    times = pd.DatetimeIndex(
        ["2026-06-01 23:00", "2026-06-02 00:00", "2026-06-03 12:00"], tz="UTC"
    )

    assert lead_days(times, RUN_AT).tolist() == [0, 1, 2]


def test_insert_forecast_writes_one_row_per_hour(db_conn, forecast):
    rows = insert_forecast(db_conn, forecast, "demo", "physical-v1", RUN_AT)

    count, first_lead, max_energy = db_conn.execute(
        "SELECT count(*), min(lead_days), max(energy_kwh)"
        " FROM solar_forecast WHERE user_id = 'demo'"
    ).fetchone()
    assert rows == 24
    assert count == 24
    assert first_lead == 0
    assert max_energy == pytest.approx(forecast["energy_kwh"].max())


def test_two_runs_on_the_same_day_are_both_kept(db_conn, forecast):
    insert_forecast(db_conn, forecast, "demo", "physical-v1", RUN_AT)
    later = RUN_AT + pd.Timedelta(hours=3)
    insert_forecast(db_conn, forecast, "demo", "physical-v1", later)

    (runs,) = db_conn.execute(
        "SELECT count(DISTINCT forecast_run_at) FROM solar_forecast WHERE user_id = 'demo'"
    ).fetchone()
    assert runs == 2


def test_negative_energy_is_rejected_by_the_database(db_conn, forecast):
    forecast.iloc[12, forecast.columns.get_loc("energy_kwh")] = -1.0

    with pytest.raises(psycopg.errors.CheckViolation):
        insert_forecast(db_conn, forecast, "demo", "physical-v1", RUN_AT)


def test_insert_forecast_saves_live_by_default(db_conn, forecast):
    insert_forecast(db_conn, forecast, "demo", "physical-v1", RUN_AT)

    sources = db_conn.execute(
        "SELECT DISTINCT source FROM solar_forecast WHERE user_id = 'demo'"
    ).fetchall()
    assert sources == [("live",)]


def test_insert_forecast_can_save_a_replay(db_conn, forecast):
    insert_forecast(db_conn, forecast, "demo", "physical-v1", RUN_AT, source="replay")

    sources = db_conn.execute(
        "SELECT DISTINCT source FROM solar_forecast WHERE user_id = 'demo'"
    ).fetchall()
    assert sources == [("replay",)]

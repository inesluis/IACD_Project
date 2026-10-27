from datetime import date

import pandas as pd
import pytest

from api.weather.forecast_check import (
    check_day_is_over,
    check_replay_sunday,
    noise_seed,
    pick_lead_columns,
    replace_replay_forecast,
    simulate_actual,
    summary_line,
    sunday_forecast,
    upsert_actual,
    validate_day,
    validate_replay,
    week_sunday,
)
from api.weather.solar_forecast import estimate_energy, insert_forecast

from api.weather.tests.conftest import CONSTANTS, KWP

VARIABLES = ["temperature_2m", "global_tilted_irradiance", "wind_speed_10m"]


# --- Plan: which day, which week --------------------------------------------


@pytest.mark.parametrize("day", [date(2026, 10, 26), date(2026, 10, 31), date(2026, 11, 1)])
def test_every_day_of_the_week_uses_the_sunday_before_it(day):
    assert week_sunday(day) == date(2026, 10, 25)


def test_a_sunday_belongs_to_the_week_that_ends_on_it():
    assert week_sunday(date(2026, 10, 25)) == date(2026, 10, 18)


def test_today_is_not_over():
    with pytest.raises(ValueError, match="not over yet"):
        check_day_is_over(date(2026, 10, 27), date(2026, 10, 27))

    check_day_is_over(date(2026, 10, 26), date(2026, 10, 27))


def test_replay_needs_a_sunday():
    with pytest.raises(ValueError, match="not a Sunday"):
        check_replay_sunday(date(2026, 10, 12), date(2026, 10, 30))


def test_replay_needs_the_week_to_be_over():
    with pytest.raises(ValueError, match="2026-10-25 is not over"):
        check_replay_sunday(date(2026, 10, 18), date(2026, 10, 25))

    check_replay_sunday(date(2026, 10, 18), date(2026, 10, 26))


# --- Transform: old forecasts, one lead per day -----------------------------


def test_pick_lead_columns_uses_k_days_ahead_for_day_k():
    time = pd.date_range(
        "2026-10-12", periods=7 * 24, freq=pd.Timedelta(hours=1), tz="UTC", name="time"
    )
    raw = pd.DataFrame(
        {f"{name}_previous_day{n}": float(n) for name in VARIABLES for n in range(1, 8)},
        index=time,
    )

    picked = pick_lead_columns(raw, date(2026, 10, 11), VARIABLES)

    assert list(picked.columns) == ["temperature_2m", "gti", "wind_speed_10m"]
    assert (picked.loc["2026-10-12"] == 1.0).all().all()
    assert (picked.loc["2026-10-15"] == 4.0).all().all()
    assert (picked.loc["2026-10-18"] == 7.0).all().all()


# --- Transform: "produced" = Sunday forecast x (1 + noise) ------------------


@pytest.fixture
def sunday_rows(weather):
    """The 24 hours of a day as sunday_forecast returns them."""
    rows = estimate_energy(weather, KWP, CONSTANTS)
    rows["lead_days"] = 1
    return rows


def test_produced_keeps_the_hours_and_columns(sunday_rows):
    produced = simulate_actual(sunday_rows, 0.10, seed=1)

    assert produced.index.equals(sunday_rows.index)
    assert list(produced.columns) == ["gti", "temperature_2m", "wind_speed_10m", "energy_kwh"]


def test_without_noise_produced_equals_the_forecast(sunday_rows):
    produced = simulate_actual(sunday_rows, 0.0, seed=1)

    assert (produced["energy_kwh"] == sunday_rows["energy_kwh"]).all()
    assert (produced["gti"] == sunday_rows["gti"]).all()


def test_noise_scales_energy_and_gti_by_the_same_factor(sunday_rows):
    produced = simulate_actual(sunday_rows, 0.10, seed=1)
    sunny = sunday_rows["gti"] > 0
    energy_factor = produced["energy_kwh"][sunny] / sunday_rows["energy_kwh"][sunny]
    gti_factor = produced["gti"][sunny] / sunday_rows["gti"][sunny]

    assert energy_factor.round(9).equals(gti_factor.round(9))
    assert (produced["temperature_2m"] == sunday_rows["temperature_2m"]).all()


def test_noise_has_a_standard_deviation_of_about_10_percent():
    time = pd.date_range("2026-01-01", periods=20_000, freq="h", tz="UTC", name="time")
    flat = pd.DataFrame(
        {"gti": 500.0, "temperature_2m": 18.0, "wind_speed_10m": 10.0, "energy_kwh": 1.0},
        index=time,
    )

    factor = simulate_actual(flat, 0.10, seed=7)["energy_kwh"]

    assert factor.mean() == pytest.approx(1.0, abs=0.005)
    assert factor.std() == pytest.approx(0.10, abs=0.005)


def test_produced_is_never_negative(sunday_rows):
    # With a huge noise many factors fall below zero; they are cut at 0.
    produced = simulate_actual(sunday_rows, 5.0, seed=3)

    assert (produced["energy_kwh"] >= 0).all()
    assert (produced["gti"] >= 0).all()
    assert (produced["energy_kwh"] == 0).sum() > (sunday_rows["energy_kwh"] == 0).sum()


def test_same_installation_and_day_give_the_same_noise(sunday_rows):
    seed = noise_seed("demo_coimbra", date(2026, 10, 26))

    first = simulate_actual(sunday_rows, 0.10, seed)
    again = simulate_actual(sunday_rows, 0.10, noise_seed("demo_coimbra", date(2026, 10, 26)))

    assert first.equals(again)
    assert seed != noise_seed("demo_coimbra", date(2026, 10, 27))


# --- Validate ----------------------------------------------------------------


@pytest.fixture
def actual(weather):
    return estimate_energy(weather, KWP, CONSTANTS)


def test_validate_day_accepts_a_full_day(actual):
    validate_day(actual, KWP, date(2026, 6, 1))


def test_validate_day_rejects_23_hours(actual):
    with pytest.raises(ValueError, match="expected the 24 hours of 2026-06-01, got 23"):
        validate_day(actual.iloc[:-1], KWP, date(2026, 6, 1))


def test_validate_day_rejects_another_day(actual):
    with pytest.raises(ValueError, match="expected the 24 hours of 2026-06-02, got 24"):
        validate_day(actual, KWP, date(2026, 6, 2))


def test_validate_day_rejects_negative_irradiance(actual):
    actual.loc[actual.index[0], "gti"] = -1.0

    with pytest.raises(ValueError, match="negative gti"):
        validate_day(actual, KWP, date(2026, 6, 1))


@pytest.fixture
def replay_week():
    time = pd.date_range(
        "2026-10-12", periods=7 * 24, freq=pd.Timedelta(hours=1), tz="UTC", name="time"
    )
    weather = pd.DataFrame(
        {"temperature_2m": 18.0, "gti": 300.0, "wind_speed_10m": 10.0}, index=time
    )
    return estimate_energy(weather, KWP, CONSTANTS)


def test_validate_replay_accepts_a_full_week(replay_week):
    validate_replay(replay_week, KWP, date(2026, 10, 11))


def test_validate_replay_rejects_a_missing_last_day(replay_week):
    with pytest.raises(ValueError, match="2026-10-12 00:00 to 2026-10-18 23:00"):
        validate_replay(replay_week.loc[:"2026-10-17"], KWP, date(2026, 10, 11))


def test_validate_replay_rejects_missing_values(replay_week):
    # Old forecasts can come back empty, e.g. 7 days ahead.
    replay_week.loc["2026-10-18", "gti"] = float("nan")

    with pytest.raises(ValueError, match="24 missing values in gti"):
        validate_replay(replay_week, KWP, date(2026, 10, 11))


# --- Report -------------------------------------------------------------------


def test_summary_line():
    line = summary_line("demo_coimbra", date(2026, 10, 26), 14.2, 12.9, "data/plots/x.png")

    assert line == (
        "demo_coimbra 2026-10-26: forecast 14.2 kWh, produced 12.9 kWh (-9%), "
        "chart data/plots/x.png"
    )


def test_summary_line_without_forecast_energy():
    line = summary_line("demo_coimbra", date(2026, 10, 26), 0.0, 0.3, "x.png")

    assert "(n/a)" in line


# --- Load: database (skipped without TEST_DATABASE_URL) ---------------------


def hours(start, end):
    """Weather plus energy for every hour from ``start`` to ``end`` (UTC), sunny at noon."""
    time = pd.date_range(start, end, freq=pd.Timedelta(hours=1), tz="UTC", name="time")
    weather = pd.DataFrame(
        {
            "temperature_2m": 18.0,
            "gti": [500.0 if 10 <= t.hour <= 15 else 0.0 for t in time],
            "wind_speed_10m": 10.0,
        },
        index=time,
    )
    return estimate_energy(weather, KWP, CONSTANTS)


def count(conn, query):
    return conn.execute(query).fetchone()[0]


def test_upsert_actual_twice_leaves_no_duplicates_and_newer_values_win(db_conn):
    day = hours("2026-10-26 00:00", "2026-10-26 23:00")
    fetched_at = pd.Timestamp("2026-10-27 08:00", tz="UTC")
    upsert_actual(db_conn, day, "demo", "physical-v1", "open-meteo-satellite", fetched_at)

    day["energy_kwh"] = 1.0
    rows = upsert_actual(db_conn, day, "demo", "physical-v1", "open-meteo-satellite", fetched_at)

    assert rows == 24
    assert count(db_conn, "SELECT count(*) FROM solar_actual WHERE user_id = 'demo'") == 24
    assert count(db_conn, "SELECT max(energy_kwh) FROM solar_actual WHERE user_id = 'demo'") == 1.0


def test_replaying_a_week_twice_replaces_only_the_replay(db_conn):
    live_at = pd.Timestamp("2026-10-11 18:00", tz="UTC")
    insert_forecast(db_conn, hours("2026-10-11 19:00", "2026-10-18 23:00"),
                    "demo", "physical-v1", live_at)
    week = hours("2026-10-12 00:00", "2026-10-18 23:00")

    replace_replay_forecast(db_conn, week, "demo", "physical-v1", date(2026, 10, 11))
    replace_replay_forecast(db_conn, week, "demo", "physical-v1", date(2026, 10, 11))

    assert count(db_conn, "SELECT count(*) FROM solar_forecast"
                          " WHERE user_id = 'demo' AND source = 'replay'") == 168
    assert count(db_conn, "SELECT count(*) FROM solar_forecast"
                          " WHERE user_id = 'demo' AND source = 'live'") == 173


def test_sunday_forecast_prefers_live_unless_replay_is_asked(db_conn):
    live_at = pd.Timestamp("2026-10-11 18:00", tz="UTC")
    insert_forecast(db_conn, hours("2026-10-11 19:00", "2026-10-18 23:00"),
                    "demo", "physical-v1", live_at)
    replace_replay_forecast(db_conn, hours("2026-10-12 00:00", "2026-10-18 23:00"),
                            "demo", "physical-v1", date(2026, 10, 11))

    run_at, source, rows = sunday_forecast(db_conn, "demo", date(2026, 10, 13))
    assert source == "live"
    assert run_at == live_at
    assert len(rows) == 24
    assert {"gti", "temperature_2m", "wind_speed_10m", "energy_kwh", "lead_days"} <= set(rows.columns)

    run_at, source, rows = sunday_forecast(db_conn, "demo", date(2026, 10, 13), source="replay")
    assert source == "replay"
    assert run_at == pd.Timestamp("2026-10-11 00:00", tz="UTC")
    assert (rows["lead_days"] == 2).all()


def test_no_sunday_forecast_names_the_week(db_conn):
    with pytest.raises(ValueError, match="no Sunday forecast for the week of 2026-10-12"):
        sunday_forecast(db_conn, "demo", date(2026, 10, 13))


def test_partial_day_is_rejected(db_conn):
    run_at = pd.Timestamp("2026-10-11 18:00", tz="UTC")
    insert_forecast(db_conn, hours("2026-10-11 19:00", "2026-10-18 17:00"),
                    "demo", "physical-v1", run_at)

    with pytest.raises(ValueError, match="covers only 18 of the 24 hours"):
        sunday_forecast(db_conn, "demo", date(2026, 10, 18))

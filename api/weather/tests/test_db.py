import pytest

from api.weather.db import check_columns, connect, ensure_schema
from api.weather.solar_forecast import CONFIG_DIR, load_yaml


def test_connect_without_url_explains_what_to_set(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match=r"DATABASE_URL.*\.env\.example"):
        connect()


def test_ensure_schema_can_run_twice(db_conn):
    ensure_schema(db_conn)
    ensure_schema(db_conn)


def test_check_columns_accepts_the_configured_history_variables(db_conn):
    config = load_yaml(CONFIG_DIR / "open_meteo.yaml")

    check_columns(db_conn, "weather_history", config["history"]["hourly"])


def test_check_columns_names_every_missing_column(db_conn):
    names = ["temperature_2m", "wind_speed_180m", "precipitation_probability"]

    with pytest.raises(ValueError, match="wind_speed_180m.*precipitation_probability"):
        check_columns(db_conn, "weather_history", names)


def test_solar_actual_has_the_forecast_columns(db_conn):
    check_columns(
        db_conn,
        "solar_actual",
        ["gti", "temperature_2m", "wind_speed_10m", "energy_kwh", "source", "fetched_at"],
    )

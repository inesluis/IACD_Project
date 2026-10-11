import pandas as pd
import pytest

from weather_api.pv_model import estimate_energy
from weather_api.store import write_forecast

from conftest import CONSTANTS, KWP

RUN_AT = pd.Timestamp("2026-06-01 06:30", tz="UTC")


@pytest.fixture
def forecast(weather):
    return estimate_energy(weather, KWP, CONSTANTS)


def test_file_is_saved_in_the_run_date_folder(forecast, tmp_path):
    path = write_forecast(forecast, "demo", "physical-v1", RUN_AT, tmp_path)

    assert path == tmp_path / "run_date=2026-06-01" / "demo.parquet"
    assert path.is_file()


def test_saved_table_has_the_expected_columns_and_values(forecast, tmp_path):
    path = write_forecast(forecast, "demo", "physical-v1", RUN_AT, tmp_path)

    table = pd.read_parquet(path)

    assert list(table.columns) == [
        "user_id",
        "forecast_run_at",
        "time",
        "temperature_2m",
        "gti",
        "wind_speed_10m",
        "energy_kwh",
        "model_version",
    ]
    assert len(table) == 24
    assert (table["user_id"] == "demo").all()
    assert (table["forecast_run_at"] == RUN_AT).all()
    assert (table["model_version"] == "physical-v1").all()
    assert table["energy_kwh"].tolist() == pytest.approx(forecast["energy_kwh"].tolist())


def test_runs_on_different_days_are_both_kept(forecast, tmp_path):
    first = write_forecast(forecast, "demo", "physical-v1", RUN_AT, tmp_path)
    next_day = RUN_AT + pd.Timedelta(days=1)
    second = write_forecast(forecast, "demo", "physical-v1", next_day, tmp_path)

    assert first != second
    assert first.is_file() and second.is_file()

import numpy as np
import pandas as pd
import pytest

from weather_api.pv_model import estimate_energy
from weather_api.validate import validate

from conftest import CONSTANTS, KWP


@pytest.fixture
def forecast(weather):
    return estimate_energy(weather, KWP, CONSTANTS)


def test_good_forecast_passes(forecast):
    validate(forecast, KWP, forecast_days=1)


def test_wrong_number_of_rows_is_rejected(forecast):
    with pytest.raises(ValueError, match="expected 24 hourly rows, got 23"):
        validate(forecast.iloc[:-1], KWP, forecast_days=1)


def test_gap_between_hours_is_rejected(forecast):
    # Still 24 rows, but the last one is two hours after the one before it.
    last_hour_late = forecast.index[-1:] + pd.Timedelta(hours=1)
    forecast.index = forecast.index[:-1].append(last_hour_late)

    with pytest.raises(ValueError, match="not continuous"):
        validate(forecast, KWP, forecast_days=1)


def test_missing_values_are_rejected(forecast):
    forecast.loc[forecast.index[-3:], "temperature_2m"] = np.nan

    with pytest.raises(ValueError, match="3 missing values in temperature_2m"):
        validate(forecast, KWP, forecast_days=1)


def test_negative_irradiance_is_rejected(forecast):
    forecast.loc[forecast.index[0], "gti"] = -1.0

    with pytest.raises(ValueError, match="negative gti"):
        validate(forecast, KWP, forecast_days=1)


def test_energy_far_above_peak_power_is_rejected(forecast):
    forecast.loc[forecast.index[12], "energy_kwh"] = 10.0

    with pytest.raises(ValueError, match="energy_kwh outside"):
        validate(forecast, KWP, forecast_days=1)

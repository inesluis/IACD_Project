"""Validate: sanity checks on a forecast before it is saved."""

import pandas as pd


def validate(forecast, kwp, forecast_days):
    """Raise ValueError describing the first problem found in ``forecast``."""
    expected_rows = forecast_days * 24
    if len(forecast) != expected_rows:
        raise ValueError(f"expected {expected_rows} hourly rows, got {len(forecast)}")

    steps = forecast.index.to_series().diff().dropna()
    if not (steps == pd.Timedelta(hours=1)).all():
        raise ValueError("the hours are not continuous")

    for column in forecast.columns:
        missing = int(forecast[column].isna().sum())
        if missing > 0:
            raise ValueError(f"{missing} missing values in {column}")

    if (forecast["gti"] < 0).any():
        raise ValueError("negative gti")

    # Output can pass the rated peak slightly on cold, very bright hours.
    max_energy = 1.2 * kwp
    energy = forecast["energy_kwh"]
    if (energy < 0).any() or (energy > max_energy).any():
        raise ValueError(f"energy_kwh outside 0 to {max_energy:.2f} kWh")

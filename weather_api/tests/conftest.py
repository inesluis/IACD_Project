import pandas as pd
import pytest

CONSTANTS = {"noct": 45.0, "gamma": -0.004, "pr": 0.8}
KWP = 3.5

# Irradiance on the panel for each hour of a sunny day, in W/m2.
GTI_DAY = [
    0, 0, 0, 0, 0, 20, 120, 300, 480, 640, 760, 820,
    840, 800, 700, 560, 400, 230, 90, 10, 0, 0, 0, 0,
]


@pytest.fixture
def weather():
    """One day of hourly weather, shaped like the output of fetch_forecast."""
    time = pd.date_range(
        "2026-06-01", periods=24, freq=pd.Timedelta(hours=1), tz="UTC", name="time"
    )
    return pd.DataFrame(
        {
            "temperature_2m": 18.0,
            "gti": [float(value) for value in GTI_DAY],
            "wind_speed_10m": 10.0,
        },
        index=time,
    )

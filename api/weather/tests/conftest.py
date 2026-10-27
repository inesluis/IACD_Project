import os

import pandas as pd
import pytest
from dotenv import load_dotenv

from api.weather.db import connect, ensure_schema

# TEST_DATABASE_URL can live in .env, like DATABASE_URL.
load_dotenv()

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


@pytest.fixture
def db_conn():
    """A database connection whose changes are all rolled back after the test.

    Skipped when TEST_DATABASE_URL is not set (in the environment or .env). The writes inside the test use
    their own transaction blocks, which become savepoints inside this one.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    conn = connect(url)
    ensure_schema(conn)
    try:
        with conn.transaction(force_rollback=True):
            yield conn
    finally:
        conn.close()

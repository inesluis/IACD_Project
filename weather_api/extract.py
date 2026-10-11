"""Extract: fetch the hourly weather forecast from Open-Meteo."""

import openmeteo_requests
import pandas as pd
import requests_cache
from retry_requests import retry


def build_client():
    """Open-Meteo client with a one-hour cache and retry on error."""
    cache_session = requests_cache.CachedSession(".cache", expire_after=3600)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    return openmeteo_requests.Client(session=retry_session)


def fetch_forecast(client, config, installation):
    """Return a DataFrame with one row per hour and one column per variable.

    The index is the time in UTC. The irradiance on the panel is in the
    column "gti" (W/m2), already adjusted for the panel's tilt and azimuth.
    """
    variables = config["hourly"]
    params = {
        "latitude": installation["latitude"],
        "longitude": installation["longitude"],
        "tilt": installation["tilt"],
        "azimuth": installation["azimuth"],
        "hourly": variables,
        "forecast_days": config["forecast_days"],
    }
    responses = client.weather_api(config["url"], params=params)
    hourly = responses[0].Hourly()

    time = pd.date_range(
        start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly.Interval()),
        inclusive="left",
        name="time",
    )

    # The API returns the variables in the same order they were requested.
    data = {}
    for position, name in enumerate(variables):
        data[name] = hourly.Variables(position).ValuesAsNumpy().astype("float64")

    weather = pd.DataFrame(data, index=time)
    return weather.rename(columns={"global_tilted_irradiance": "gti"})

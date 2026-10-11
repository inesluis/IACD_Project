# IACD-project

## Solar production forecast

Forecasts the hourly energy (kWh) each solar installation will produce over the
next 7 days, from the Open-Meteo weather forecast and a physical panel model.

The code is in `weather_api/`, one file per stage:

| File | Stage |
|---|---|
| `extract.py` | fetch the hourly weather forecast from Open-Meteo |
| `pv_model.py` | turn irradiance and temperature into energy (kWh) |
| `validate.py` | sanity checks before saving |
| `store.py` | save the forecast as parquet |
| `pipeline.py` | run the stages for every installation |

Run it from the repository root:

    python -m weather_api.pipeline

Each run writes `data/forecasts/run_date=YYYY-MM-DD/<user_id>.parquet`.
Timestamps are in UTC.

Installations are listed in `weather_api/configs/installations.yaml`. Azimuth
follows the Open-Meteo convention, not compass degrees: 0 = south, -90 = east,
90 = west. Model constants and the forecast length are in
`weather_api/configs/open_meteo.yaml`.

Tests, also from the repository root:

    python -m pytest weather_api/tests
# IACD-project

## Database

Forecasts and weather history are stored in PostgreSQL, run with Docker.
The first time, copy the example settings and change the password:

    copy .env.example .env

Then start the database (from the repository root):

    docker compose up -d db

The tables are defined in `database/schema.sql`. The pipelines create any table
that is missing when they start, and read the connection from `DATABASE_URL`
in `.env`. All timestamps are stored in UTC.

## Solar production forecast

Forecasts the hourly energy (kWh) each solar installation will produce over the
next 7 days, from the Open-Meteo weather forecast and a physical panel model.

The code is in `api/weather/solar_forecast.py`, in one section per stage:

| Section | Function | What it does |
|---|---|---|
| Extract | `fetch_forecast` | fetch the hourly weather forecast from Open-Meteo |
| Transform | `estimate_energy` | turn irradiance and temperature into energy (kWh) |
| Validate | `validate` | sanity checks before saving |
| Load | `insert_forecast` | save the forecast in the `solar_forecast` table |
| Pipeline | `main` | run the stages for every installation |

Run it from the repository root:

    python -m api.weather.solar_forecast

A run covers the rest of its own day plus 7 full days (UTC), so a Sunday run
covers Monday 00:00 to the next Sunday 23:00.

Every run is kept, identified by `forecast_run_at`. The column `lead_days`
says how far ahead each hour is: 0 for the day of the run, 1 for the next
day (D+1), and so on. The column `source` is `live` for these runs and
`replay` for past Sundays rebuilt by the forecast check (below).

## Forecast check

Compares each day with the forecast made on the Sunday before its week
(Monday to Sunday, UTC): Monday with the 1-day-ahead values, Tuesday with the
2-days-ahead values, and so on. Run it the day after:

    python -m api.weather.forecast_check                      # yesterday (UTC)
    python -m api.weather.forecast_check --date 2026-10-26    # a chosen day
    python -m api.weather.forecast_check --replay 2026-10-11  # a past Sunday: whole week

For each installation it saves what the panels "produced" in the table
`solar_actual` (one row per hour; a re-run overwrites it) and draws
`data/plots/<user_id>_<YYYY-MM-DD>_check.png` with both curves.

- **"Produced"** is not metered, it is simulated: the Sunday forecast of each
  hour times (1 + noise), with noise ~ N(0, 10%) drawn independently per hour
  and the result never below 0 (`noise_std` in the `check` section of
  `open_meteo.yaml`). The noise is seeded by installation and day, so checking
  the same day again gives the same values. Irradiance (`gti`) gets the same
  factor as the energy; temperature and wind are kept as forecast.
- **No Sunday forecast for that week** (or one that does not cover the whole
  day): that installation fails with a message; nothing is filled in.
- **`--replay`** shows a whole past week without waiting for it. It rebuilds
  the Sunday forecast from Open-Meteo's Previous Runs API: for the day Sunday + k
  it takes what the forecast said k days before. That is close to, but not
  exactly, the run of that Sunday. It is saved with `source = 'replay'`,
  replacing an earlier replay of the same Sunday; live runs are never touched.
  The daily check uses the live run of a Sunday when there is one.

To plot tomorrow's forecast for each installation (fetched live, not read
from the database):

    python -m api.weather.plot_tomorrow

Installations are listed in `api/weather/configs/installations.yaml`. Azimuth
follows the Open-Meteo convention, not compass degrees: 0 = south, -90 = east,
90 = west. Model constants and the forecast length are in
`api/weather/configs/open_meteo.yaml`.

## Weather history

Keeps the hourly weather that actually happened for each installation, from
the ERA5 reanalysis (Open-Meteo archive, model `era5_seamless`), in the
`weather_history` table. Compared with the stored forecasts, it shows how far
the forecast was from reality.

ERA5 is published about 6 days late, so the history goes up to 7 days ago
(`lag_days` in the `history` section of `open_meteo.yaml`).

    python -m api.weather.weather_history

The first run loads everything since `start_date` (2025-01-01). After that,
each run adds the new day; a day missed by a failed or skipped run is caught
up automatically the next time.

To fetch a chosen range instead, give `--start` and/or `--end` (UTC days).
Hours already stored in that range are overwritten, so this also re-fetches
days. A missing `--start` means `start_date`; a missing `--end` means 7 days
ago. An `--end` later than that is refused, since the reanalysis is not
complete yet.

    python -m api.weather.weather_history --start 2024-01-01 --end 2024-12-31

## Tests

From the repository root:

    python -m pytest api/weather/tests

The tests that need the database are skipped unless `TEST_DATABASE_URL` is
set (see `.env.example`). They roll back everything they write.

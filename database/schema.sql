-- SolarCast database schema.
-- Every statement is IF NOT EXISTS, so it is safe to run on an existing database.
-- All timestamps are UTC; "time" is the end of the hour the row describes.
-- The CHECKs repeat the Python validation, so no bad row gets in by another path.

-- Hourly weather that actually happened (ERA5 reanalysis), one row per installation and hour.
CREATE TABLE IF NOT EXISTS weather_history (
    user_id     text        NOT NULL,
    time        timestamptz NOT NULL,

    temperature_2m        double precision NOT NULL,
    relative_humidity_2m  double precision NOT NULL CHECK (relative_humidity_2m BETWEEN 0 AND 100),

    -- Radiation, mean over the preceding hour, W/m2
    shortwave_radiation       double precision NOT NULL CHECK (shortwave_radiation >= 0),
    direct_radiation          double precision NOT NULL CHECK (direct_radiation >= 0),
    diffuse_radiation         double precision NOT NULL CHECK (diffuse_radiation >= 0),
    direct_normal_irradiance  double precision NOT NULL CHECK (direct_normal_irradiance >= 0),
    global_tilted_irradiance  double precision NOT NULL CHECK (global_tilted_irradiance >= 0),
    terrestrial_radiation     double precision NOT NULL CHECK (terrestrial_radiation >= 0),

    -- Radiation, value at the hour, W/m2
    shortwave_radiation_instant       double precision NOT NULL CHECK (shortwave_radiation_instant >= 0),
    direct_radiation_instant          double precision NOT NULL CHECK (direct_radiation_instant >= 0),
    diffuse_radiation_instant         double precision NOT NULL CHECK (diffuse_radiation_instant >= 0),
    direct_normal_irradiance_instant  double precision NOT NULL CHECK (direct_normal_irradiance_instant >= 0),
    global_tilted_irradiance_instant  double precision NOT NULL CHECK (global_tilted_irradiance_instant >= 0),
    terrestrial_radiation_instant     double precision NOT NULL CHECK (terrestrial_radiation_instant >= 0),

    -- Clouds, %
    cloud_cover       double precision NOT NULL CHECK (cloud_cover BETWEEN 0 AND 100),
    cloud_cover_low   double precision NOT NULL CHECK (cloud_cover_low BETWEEN 0 AND 100),
    cloud_cover_mid   double precision NOT NULL CHECK (cloud_cover_mid BETWEEN 0 AND 100),
    cloud_cover_high  double precision NOT NULL CHECK (cloud_cover_high BETWEEN 0 AND 100),

    -- Wind, km/h
    wind_speed_10m   double precision NOT NULL CHECK (wind_speed_10m >= 0),
    wind_speed_80m   double precision NOT NULL CHECK (wind_speed_80m >= 0),
    wind_speed_100m  double precision NOT NULL CHECK (wind_speed_100m >= 0),
    wind_speed_120m  double precision NOT NULL CHECK (wind_speed_120m >= 0),

    -- Precipitation over the preceding hour, mm
    precipitation  double precision NOT NULL CHECK (precipitation >= 0),

    source      text        NOT NULL,   -- the Open-Meteo model, e.g. 'era5_seamless'
    fetched_at  timestamptz NOT NULL,
    PRIMARY KEY (user_id, time)
);

-- Hourly solar production forecast. Every run is kept: the run time is in the key.
CREATE TABLE IF NOT EXISTS solar_forecast (
    user_id          text        NOT NULL,
    forecast_run_at  timestamptz NOT NULL,
    time             timestamptz NOT NULL,
    lead_days        smallint    NOT NULL CHECK (lead_days >= 0),  -- 0 = day of the run, 1 = next day, ...
    gti              double precision NOT NULL CHECK (gti >= 0),   -- W/m2 on the panel plane
    temperature_2m   double precision NOT NULL,
    wind_speed_10m   double precision NOT NULL,
    energy_kwh       double precision NOT NULL CHECK (energy_kwh >= 0),
    model_version    text        NOT NULL,
    PRIMARY KEY (user_id, forecast_run_at, time)
);

-- 'live' for a run of solar_forecast.py, 'replay' for a past Sunday rebuilt
-- from Open-Meteo's previous runs by forecast_check.py --replay.
ALTER TABLE solar_forecast ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'live';

-- Hourly "produced" energy, simulated: the Sunday forecast x (1 + noise),
-- noise ~ N(0, 10%), >= 0. One row per installation and hour; a re-run overwrites it.
CREATE TABLE IF NOT EXISTS solar_actual (
    user_id         text        NOT NULL,
    time            timestamptz NOT NULL,
    gti             double precision NOT NULL CHECK (gti >= 0),   -- W/m2 on the panel plane
    temperature_2m  double precision NOT NULL,
    wind_speed_10m  double precision NOT NULL,
    energy_kwh      double precision NOT NULL CHECK (energy_kwh >= 0),
    model_version   text        NOT NULL,
    source          text        NOT NULL,   -- e.g. 'simulated-noise'
    fetched_at      timestamptz NOT NULL,
    PRIMARY KEY (user_id, time)
);

-- EPREL appliance catalogue, one row per registered model (api/appliances/eprel_sync.py).
-- Typed columns are filled where the group has them; raw keeps the full record.
CREATE TABLE IF NOT EXISTS appliance_model (
    registration_number  bigint PRIMARY KEY,   -- eprelRegistrationNumber
    product_group        text NOT NULL,        -- EPREL url_code, e.g. 'washingmachines2019'
    appliance_type       text NOT NULL CHECK (appliance_type IN (
        'maq_lavar', 'maq_secar', 'maq_loica', 'maq_lavar_secar', 'frigorifico', 'forno',
        'exaustor', 'ecra', 'ar_condicionado', 'termoacumulador', 'deposito_agua_quente',
        'aquecedor', 'lampada')),
    brand                text NOT NULL,        -- supplierOrTrademark, as written
    brand_key            text NOT NULL,        -- normalised brand
    model_identifier     text NOT NULL,        -- modelIdentifier, as written
    model_key            text NOT NULL,        -- normalised model
    energy_class         text,
    energy_kwh_cycle     double precision CHECK (energy_kwh_cycle >= 0),
    programme_minutes    integer          CHECK (programme_minutes >= 0),
    energy_kwh_annual    double precision CHECK (energy_kwh_annual >= 0),
    power_on_w           double precision CHECK (power_on_w >= 0),
    capacity             double precision CHECK (capacity >= 0),
    capacity_unit        text CHECK (capacity_unit IN ('kg', 'place_settings', 'litres', 'inches', 'kw')),
    on_market_start      date,
    on_market_end        date,
    raw                  jsonb       NOT NULL,
    fetched_at           timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS appliance_model_search
    ON appliance_model (appliance_type, brand_key, model_key);

-- The appliances each user owns, matched to the catalogue (api/appliances/match.py).
-- Several rows per type are allowed (two TVs, a heater per room).
CREATE TABLE IF NOT EXISTS user_appliance (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id              text NOT NULL,
    appliance_type       text NOT NULL CHECK (appliance_type IN (
        'maq_lavar', 'maq_secar', 'maq_loica', 'maq_lavar_secar', 'frigorifico', 'forno',
        'exaustor', 'ecra', 'ar_condicionado', 'termoacumulador', 'deposito_agua_quente',
        'aquecedor', 'lampada')),
    input_brand          text NOT NULL,        -- exactly what the user gave
    input_model          text NOT NULL,
    registration_number  bigint REFERENCES appliance_model,  -- null when nothing matched
    match_kind           text NOT NULL CHECK (match_kind IN ('exact', 'close', 'weak', 'none')),
    score                double precision CHECK (score BETWEEN 0 AND 1),
    quantity             smallint NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    matched_at           timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS user_appliance_user ON user_appliance (user_id);

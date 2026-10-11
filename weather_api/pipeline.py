"""Solar production forecast pipeline: extract, transform, validate, load.

Run from the repository root:

    python -m weather_api.pipeline
"""

import sys
from pathlib import Path

import pandas as pd
import yaml

from weather_api.extract import build_client, fetch_forecast
from weather_api.pv_model import estimate_energy
from weather_api.store import write_forecast
from weather_api.validate import validate

CONFIG_DIR = Path(__file__).parent / "configs"
OUTPUT_DIR = Path("data") / "forecasts"


def load_yaml(path):
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def main():
    config = load_yaml(CONFIG_DIR / "open_meteo.yaml")
    installations = load_yaml(CONFIG_DIR / "installations.yaml")["installations"]

    client = build_client()
    run_at = pd.Timestamp.now(tz="UTC")  # same timestamp for every installation

    failed = []
    for installation in installations:
        user_id = installation["user_id"]
        kwp = installation["kwp"]
        try:
            weather = fetch_forecast(client, config, installation)
            forecast = estimate_energy(weather, kwp, config["constants"])
            validate(forecast, kwp, config["forecast_days"])
            path = write_forecast(
                forecast, user_id, config["model_version"], run_at, OUTPUT_DIR
            )
            total = forecast["energy_kwh"].sum()
            print(f"{user_id}: {total:.1f} kWh forecast, saved to {path}")
        except Exception as error:
            # One installation failing must not stop the others.
            print(f"{user_id}: FAILED - {error}")
            failed.append(user_id)

    # A non-zero exit code lets a scheduler notice that something failed.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

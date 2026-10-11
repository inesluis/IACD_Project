"""Load: save a forecast as a parquet file."""

from pathlib import Path


def write_forecast(forecast, user_id, model_version, run_at, base_dir):
    """Save the forecast and return the path of the file written.

    One file per installation and per run date:
        <base_dir>/run_date=YYYY-MM-DD/<user_id>.parquet
    Earlier days are kept, so a forecast can always be traced back.
    """
    table = forecast.reset_index()  # "time" becomes a normal column
    table.insert(0, "user_id", user_id)
    table.insert(1, "forecast_run_at", run_at)
    table["model_version"] = model_version

    folder = Path(base_dir) / f"run_date={run_at:%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{user_id}.parquet"
    table.to_parquet(path, index=False)
    return path

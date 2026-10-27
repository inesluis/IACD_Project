"""Plot tomorrow's (D+1) hourly solar production for each installation.

Reuses the Extract and Transform stages of solar_forecast.py: fetch the
Open-Meteo forecast, estimate the energy per hour, then keep only the hours
of tomorrow in local time and draw them as a bar chart.

Run from the repository root:

    python -m api.weather.plot_tomorrow

Each run writes data/plots/<user_id>_<YYYY-MM-DD>.png and opens the chart.
"""

import sys
from datetime import timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from api.weather.solar_forecast import (
    CONFIG_DIR,
    build_client,
    estimate_energy,
    fetch_forecast,
    load_yaml,
)

TIMEZONE = "Europe/Lisbon"
PLOT_DIR = Path("data") / "plots"

BAR_COLOR = "#2a78d6"
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"


def tomorrow_only(forecast, timezone=TIMEZONE, today=None):
    """Return the rows of ``forecast`` that fall on tomorrow, in local time.

    The forecast index is in UTC; it is converted to ``timezone`` first so
    that "tomorrow" means 00:00 to 23:00 on the local clock. ``today`` is the
    local date (default: now). Dates are compared, not "midnight + 24 hours",
    which misses tomorrow on the days the clocks change (23 or 25 hours).
    """
    local = forecast.tz_convert(timezone)
    today = today or pd.Timestamp.now(tz=timezone).date()
    tomorrow = today + timedelta(days=1)
    return local[local.index.date == tomorrow]


def plot_day(day, user_id, kwp):
    """Draw the hourly energy of one day and return the matplotlib figure."""
    date = day.index[0].date()
    total = day["energy_kwh"].sum()
    peak_time = day["energy_kwh"].idxmax()
    peak = day["energy_kwh"].max()

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)

    ax.bar(day.index.hour, day["energy_kwh"], width=0.8, color=BAR_COLOR)

    fig.suptitle(
        f"Produção solar prevista — {user_id} ({kwp} kWp) — {date:%d/%m/%Y}",
        x=0.01, ha="left", color=TEXT_PRIMARY, fontsize=13,
    )
    ax.set_title(
        f"Total: {total:.1f} kWh  ·  Pico: {peak:.2f} kWh às {peak_time:%H}h",
        loc="left", color=TEXT_SECONDARY, fontsize=10,
    )
    style_hourly_axes(ax, "Hora (hora local)")
    fig.tight_layout()
    return fig


def style_hourly_axes(ax, xlabel):
    """Axes for a chart of the 24 hours of a day: kWh from 0, recessive grid, no box."""
    ax.set_xlabel(xlabel, color=TEXT_SECONDARY)
    ax.set_ylabel("Energia (kWh)", color=TEXT_SECONDARY)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlim(-0.6, 23.6)
    ax.set_ylim(bottom=0)

    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(TEXT_SECONDARY)
    ax.tick_params(colors=TEXT_SECONDARY, length=0)


def main():
    config = load_yaml(CONFIG_DIR / "open_meteo.yaml")
    installations = load_yaml(CONFIG_DIR / "installations.yaml")["installations"]
    client = build_client()
    PLOT_DIR.mkdir(parents=True, exist_ok=True)

    failed = []
    for installation in installations:
        user_id = installation["user_id"]
        kwp = installation["kwp"]
        try:
            weather = fetch_forecast(client, config, installation)
            forecast = estimate_energy(weather, kwp, config["constants"])
            day = tomorrow_only(forecast)
            # 23 or 25 hours on the days the clocks change.
            if len(day) < 23:
                raise ValueError(f"only {len(day)} hours of tomorrow in the forecast")

            fig = plot_day(day, user_id, kwp)
            path = PLOT_DIR / f"{user_id}_{day.index[0]:%Y-%m-%d}.png"
            fig.savefig(path, dpi=150)
            print(f"{user_id}: {day['energy_kwh'].sum():.1f} kWh tomorrow, chart saved to {path}")
        except Exception as error:
            print(f"{user_id}: FAILED - {error}")
            failed.append(user_id)

    plt.show()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

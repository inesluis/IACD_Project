"""Transform: turn hourly weather into hourly solar energy."""


def estimate_energy(weather, kwp, constants):
    """Return a copy of ``weather`` with a new column "energy_kwh".

    weather:   DataFrame with columns "gti" (W/m2) and "temperature_2m" (C)
    kwp:       installed peak power of the panels, in kW
    constants: dict with "noct", "gamma" and "pr" (see open_meteo.yaml)

    "gti" is the mean irradiance over the hour, so the mean power in kW over
    that hour is also the energy in kWh.
    """
    gti = weather["gti"]

    # Panels get hotter than the air when the sun shines on them.
    cell_temperature = weather["temperature_2m"] + (constants["noct"] - 20) / 800 * gti

    # Hot cells produce less: gamma is negative.
    temperature_factor = 1 + constants["gamma"] * (cell_temperature - 25)

    power_kw = kwp * (gti / 1000) * temperature_factor * constants["pr"]

    result = weather.copy()
    result["energy_kwh"] = power_kw.clip(lower=0)
    return result

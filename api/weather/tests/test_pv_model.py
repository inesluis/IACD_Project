import pytest

from api.weather.solar_forecast import estimate_energy

from api.weather.tests.conftest import CONSTANTS, KWP


def test_no_sun_gives_no_energy(weather):
    weather["gti"] = 0.0

    result = estimate_energy(weather, KWP, CONSTANTS)

    assert (result["energy_kwh"] == 0).all()


def test_standard_test_conditions_give_kwp_times_pr(weather):
    # 1000 W/m2 with the cell at 25 C: cell = air + (45 - 20) / 800 * 1000 = air + 31.25
    weather["gti"] = 1000.0
    weather["temperature_2m"] = -6.25

    result = estimate_energy(weather, KWP, CONSTANTS)

    assert result["energy_kwh"].tolist() == pytest.approx([3.5 * 0.8] * 24)


def test_hotter_air_lowers_the_output(weather):
    cool = estimate_energy(weather, KWP, CONSTANTS)
    weather["temperature_2m"] = 35.0
    hot = estimate_energy(weather, KWP, CONSTANTS)

    sunny = weather["gti"] > 0
    assert (hot.loc[sunny, "energy_kwh"] < cool.loc[sunny, "energy_kwh"]).all()


def test_double_the_panels_gives_double_the_energy(weather):
    single = estimate_energy(weather, KWP, CONSTANTS)
    double = estimate_energy(weather, KWP * 2, CONSTANTS)

    assert double["energy_kwh"].sum() == pytest.approx(single["energy_kwh"].sum() * 2)


def test_energy_is_never_negative(weather):
    weather["gti"] = -5.0

    result = estimate_energy(weather, KWP, CONSTANTS)

    assert (result["energy_kwh"] == 0).all()

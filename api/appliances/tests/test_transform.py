from datetime import date, datetime, timezone

from api.appliances.eprel_sync import (
    MODEL_COLUMNS,
    get_path,
    missing_fields,
    missing_required,
    to_row,
)

FETCHED = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def wm_mapping(config):
    return config["types"]["maq_lavar"]["groups"]["washingmachines2019"]


def wm_row(record, config):
    values = to_row(record, "washingmachines2019", "maq_lavar", wm_mapping(config), FETCHED)
    return dict(zip(MODEL_COLUMNS, values))


def oven_row(record, config):
    mapping = config["types"]["forno"]["groups"]["ovens"]
    return dict(zip(MODEL_COLUMNS, to_row(record, "ovens", "forno", mapping, FETCHED)))


def test_washing_machine_row(wm_record, config):
    row = wm_row(wm_record, config)

    assert row["registration_number"] == 352460
    assert row["product_group"] == "washingmachines2019"
    assert row["appliance_type"] == "maq_lavar"
    assert row["brand"] == "LG Electronics" and row["brand_key"] == "lg electronics"
    assert row["model_identifier"] == "GC3V708S2" and row["model_key"] == "GC3V708S2"
    assert row["energy_class"] == "A"
    assert row["energy_kwh_cycle"] == 0.473 and row["programme_minutes"] == 218
    assert row["energy_kwh_annual"] is None and row["power_on_w"] is None
    assert row["capacity"] == 8.0 and row["capacity_unit"] == "kg"
    assert row["on_market_start"] == date(2020, 9, 24)
    assert row["on_market_end"] == date(2050, 12, 31)
    assert row["raw"].obj == wm_record
    assert row["fetched_at"] == FETCHED


def test_oven_reads_the_first_cavity(oven_record, config):
    row = oven_row(oven_record, config)

    assert row["energy_kwh_cycle"] == 0.78
    assert row["capacity"] == 65.0 and row["capacity_unit"] == "litres"


def test_oven_falls_back_to_fan_forced(oven_record, config):
    oven_record["cavities"][0]["energyConsumptionCycle"] = None
    oven_record["cavities"][0]["energyConsumptionCycleFanForced"] = 0.69

    assert oven_row(oven_record, config)["energy_kwh_cycle"] == 0.69


def test_empty_list_and_null_fields_give_null(oven_record, config):
    oven_record["cavities"] = []
    oven_record["energyClass"] = None

    row = oven_row(oven_record, config)

    assert row["energy_kwh_cycle"] is None and row["capacity"] is None
    assert row["energy_class"] is None
    assert row["on_market_end"] is None


def test_number_sent_as_string_is_converted(wm_record, config):
    wm_record["ratedCapacity"] = "8"
    wm_record["programmeDurationRated"] = "218"

    row = wm_row(wm_record, config)

    assert row["capacity"] == 8.0 and row["programme_minutes"] == 218


def test_value_that_is_not_a_number_gives_null(wm_record, config):
    wm_record["energyConsPerCycle"] = "n/a"
    wm_record["ratedCapacity"] = {"value": 8}

    row = wm_row(wm_record, config)

    assert row["energy_kwh_cycle"] is None and row["capacity"] is None


def test_no_end_date_9999_is_null(wm_record, config):
    wm_record["onMarketEndDate"] = [9999, 12, 31]

    assert wm_row(wm_record, config)["on_market_end"] is None


def test_malformed_date_is_null(wm_record, config):
    wm_record["onMarketStartDate"] = [2020, 13]

    assert wm_row(wm_record, config)["on_market_start"] is None


def test_get_path_reads_nested_fields():
    record = {"a": {"b": 1}, "l": [{"x": 2}], "n": None}

    assert get_path(record, "a.b") == 1
    assert get_path(record, "l[0].x") == 2
    assert get_path(record, "l[1].x") is None
    assert get_path(record, "n.b") is None
    assert get_path(record, "missing") is None
    assert get_path(record, ["missing", "a.b"]) == 1


def test_missing_required_names_the_field():
    record = {"eprelRegistrationNumber": "1", "modelIdentifier": " "}

    assert missing_required(record) == "modelIdentifier"


def test_missing_required_is_none_for_a_complete_record(wm_record):
    assert missing_required(wm_record) is None


def test_missing_fields_lists_null_typed_columns(wm_record, config):
    del wm_record["programmeDurationRated"]

    assert missing_fields(wm_record, wm_mapping(config)) == ["programme_minutes"]


def test_negative_infinite_and_nan_numbers_give_null(wm_record, config):
    wm_record["energyConsPerCycle"] = -3
    wm_record["ratedCapacity"] = "nan"
    wm_record["programmeDurationRated"] = "inf"

    row = wm_row(wm_record, config)

    assert row["energy_kwh_cycle"] is None
    assert row["capacity"] is None
    assert row["programme_minutes"] is None


def test_minutes_too_large_for_the_column_give_null(wm_record, config):
    wm_record["programmeDurationRated"] = 2**31

    assert wm_row(wm_record, config)["programme_minutes"] is None

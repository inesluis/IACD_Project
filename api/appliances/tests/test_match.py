from datetime import datetime, timezone

import pytest

from api.appliances.eprel_sync import load_config, save_rows, to_row
from api.appliances.match import find_match, list_brands, list_models, score_model, suggest_brands

FETCHED = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
CONFIG = load_config()

# Made-up brands and registration numbers far above EPREL's, so seeded rows never clash with real ones.
BASE = 9_000_000_000


def seed(conn, appliance_type, group, brand, model, reg, start=None):
    record = {
        "eprelRegistrationNumber": str(BASE + reg),
        "modelIdentifier": model,
        "supplierOrTrademark": brand,
        "energyClass": "A",
        "onMarketStartDate": start,
    }
    mapping = CONFIG["types"][appliance_type]["groups"][group]
    save_rows(conn, [to_row(record, group, appliance_type, mapping, FETCHED)])


# ---------------------------------------------------------------- score_model

def test_equal_keys_score_1():
    assert score_model("F4WV308S4W", "F4WV308S4W") == 1.0


def test_suffix_variant_is_at_least_0_9():
    assert score_model("WW90T534DAW", "WW90T534DAWS3") >= 0.9


def test_short_prefix_gets_no_bonus():
    assert score_model("F4", "F4WV308S4W") < 0.8


def test_unrelated_is_low():
    assert score_model("F4WV308S4W", "XQ12") < 0.5


# ---------------------------------------------------------------- find_match

def test_exact_match_ignores_case_and_punctuation(db_conn):
    seed(db_conn, "maq_lavar", "washingmachines2019", "Zyxlo", "F4WV308S4W", 1)

    match = find_match(db_conn, "maq_lavar", "Zyxlo", "f4wv-308 s4w")

    assert match.match_kind == "exact" and match.score == 1.0
    assert match.model["registration_number"] == BASE + 1


def test_brand_spelling_variant_finds_the_same_models(db_conn):
    seed(db_conn, "maq_lavar", "washingmachines2019", "Zyxlo Electronics Inc.", "F4WV308S4W", 1)

    match = find_match(db_conn, "maq_lavar", "zyxlo electronics", "F4WV308S4W")

    assert match.match_kind == "exact"


def test_close_match_on_missing_suffix(db_conn):
    seed(db_conn, "maq_lavar", "washingmachines2019", "Qorvan", "WW90T534DAW/S3", 1)

    match = find_match(db_conn, "maq_lavar", "Qorvan", "WW90T534DAW")

    assert match.match_kind == "close" and match.score >= 0.9
    assert match.model["model_identifier"] == "WW90T534DAW/S3"


def test_weak_match_is_still_returned_and_flagged(db_conn):
    seed(db_conn, "maq_lavar", "washingmachines2019", "Brimtek", "WAN28281ES", 1)

    match = find_match(db_conn, "maq_lavar", "Brimtek", "XQ12")

    assert match.match_kind == "weak" and match.score < 0.8
    assert match.model["registration_number"] == BASE + 1


def test_best_match_wins_and_others_are_candidates(db_conn):
    for reg, model in enumerate(["F4WV308S4W", "F4WV308S4E", "F4WV310S6E", "F2J5", "F94V", "ZZ"], 1):
        seed(db_conn, "maq_lavar", "washingmachines2019", "Zyxlo", model, reg)

    match = find_match(db_conn, "maq_lavar", "Zyxlo", "F4WV308S4W")

    assert match.model["model_identifier"] == "F4WV308S4W"
    assert len(match.candidates) == 4
    assert match.candidates[0]["model_identifier"] == "F4WV308S4E"
    scores = [c["score"] for c in match.candidates]
    assert scores == sorted(scores, reverse=True)


def test_old_and_new_registration_prefers_newer_group(db_conn):
    seed(db_conn, "maq_lavar", "washingmachines", "Zyxlo", "F4J3TN5WE", 7000)
    seed(db_conn, "maq_lavar", "washingmachines2019", "Zyxlo", "F4J3TN5WE", 900000)

    match = find_match(db_conn, "maq_lavar", "Zyxlo", "F4J3TN5WE")

    assert match.model["registration_number"] == BASE + 900000


def test_same_group_tie_prefers_latest_on_market_start(db_conn):
    seed(db_conn, "maq_lavar", "washingmachines2019", "Zyxlo", "AB12345X", 1, start=[2019, 1, 1])
    seed(db_conn, "maq_lavar", "washingmachines2019", "Zyxlo", "AB12345Y", 2, start=[2022, 1, 1])

    match = find_match(db_conn, "maq_lavar", "Zyxlo", "AB12345")

    assert match.model["registration_number"] == BASE + 2


def test_unknown_brand_returns_none_with_suggestions(db_conn):
    seed(db_conn, "maq_lavar", "washingmachines2019", "Qorvan", "WW90T534DAW", 1)

    match = find_match(db_conn, "maq_lavar", "Qorvam", "WW90T534DAW")

    assert match.match_kind == "none" and match.model is None and match.score is None
    assert "Qorvan" in match.brand_suggestions


def test_other_types_are_not_candidates(db_conn):
    seed(db_conn, "frigorifico", "refrigeratingappliances2019", "Kelvra", "RCNA366", 1)

    match = find_match(db_conn, "maq_lavar", "Kelvra", "RCNA366")

    assert match.match_kind == "none"


def test_empty_model_is_rejected(db_conn):
    with pytest.raises(ValueError, match="model is empty"):
        find_match(db_conn, "maq_lavar", "Zyxlo", " - ")


def test_empty_brand_is_rejected(db_conn):
    with pytest.raises(ValueError, match="brand is empty"):
        find_match(db_conn, "maq_lavar", " ", "F4WV308S4W")


def test_unknown_type_is_rejected(db_conn):
    with pytest.raises(ValueError, match="unknown appliance type"):
        find_match(db_conn, "fridge", "Zyxlo", "F4WV308S4W")


# ---------------------------------------------------------------- lists

def test_list_brands_merges_spellings(db_conn):
    seed(db_conn, "exaustor", "rangehoods", "Zentora Electronics", "DLH 686 T", 1)
    seed(db_conn, "exaustor", "rangehoods", "Zentora Electronics", "DLH 786 T", 2)
    seed(db_conn, "exaustor", "rangehoods", "ZENTORA ELECTRONICS INC.", "DLH 986 T", 3)

    brands = list_brands(db_conn, "exaustor")

    assert brands.count("Zentora Electronics") == 1
    assert "ZENTORA ELECTRONICS INC." not in brands


def test_list_models_of_a_brand(db_conn):
    seed(db_conn, "exaustor", "rangehoods", "Zentora Electronics", "DLH 786 T", 2)
    seed(db_conn, "exaustor", "rangehoods", "Zentora Electronics Inc.", "DLH 686 T", 1)

    models = list_models(db_conn, "exaustor", "zentora electronics")

    assert [m["model_identifier"] for m in models] == ["DLH 686 T", "DLH 786 T"]
    assert set(models[0]) == {
        "registration_number", "model_identifier", "energy_class", "energy_kwh_cycle",
        "programme_minutes", "energy_kwh_annual", "power_on_w", "capacity", "capacity_unit",
    }


# ---------------------------------------------------------------- suggest_brands

def test_short_brand_suggests_brands_starting_with_it():
    names = {"lg electronics": "LG Electronics", "lgx": "LGX", "beko": "Beko"}

    assert suggest_brands("lg", names)[0] == "LG Electronics"
    assert "Beko" not in suggest_brands("lg", names)


def test_misspelled_brand_is_suggested():
    names = {"samsung": "Samsung", "beko": "Beko"}

    assert suggest_brands("samsumg", names) == ["Samsung"]

import pytest

from api.appliances.match import normalize_brand, normalize_model


@pytest.mark.parametrize("raw", ["F4WV-308 S4W", "f4wv308s4w", " F4WV.308/S4W "])
def test_model_keeps_only_letters_and_digits_uppercase(raw):
    assert normalize_model(raw) == "F4WV308S4W"


def test_model_of_punctuation_only_is_empty():
    assert normalize_model(" - / ") == ""


@pytest.mark.parametrize(
    "raw",
    ["LG Electronics", "LG Electronics Inc.", "lg electronics, inc", "LG  ELECTRONICS GmbH"],
)
def test_brand_variants_share_one_key(raw):
    assert normalize_brand(raw) == "lg electronics"


def test_brand_removes_accents():
    assert normalize_brand("Électrolux") == normalize_brand("Electrolux") == "electrolux"


def test_brand_made_only_of_a_company_word_is_kept():
    assert normalize_brand("CO") == "co"


@pytest.mark.parametrize("raw", ["Fagor S.A.", "Fagor SA", "FAGOR s.a"])
def test_brand_abbreviations_with_dots_merge(raw):
    assert normalize_brand(raw) == "fagor"

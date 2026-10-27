import psycopg
import pytest

from api.appliances.eprel_sync import MODEL_COLUMNS
from api.weather.db import check_columns


def test_config_has_the_13_types_and_19_groups(config):
    assert len(config["types"]) == 13
    groups = [g for t in config["types"].values() for g in t["groups"]]
    assert len(groups) == 19 and len(set(groups)) == 19
    assert "lightsources" not in groups


def test_groups_are_newest_first(config):
    assert list(config["types"]["maq_lavar"]["groups"]) == ["washingmachines2019", "washingmachines"]
    assert list(config["types"]["ecra"]["groups"]) == ["electronicdisplays", "televisions"]


def test_capacity_units_are_known(config):
    units = {
        g.get("capacity_unit")
        for t in config["types"].values()
        for g in t["groups"].values()
    }
    assert units <= {None, "kg", "place_settings", "litres", "inches", "kw"}


def test_schema_has_the_catalogue_columns(db_conn):
    check_columns(db_conn, "appliance_model", MODEL_COLUMNS)
    check_columns(
        db_conn,
        "user_appliance",
        ["id", "user_id", "appliance_type", "input_brand", "input_model",
         "registration_number", "match_kind", "score", "quantity", "matched_at"],
    )


def test_schema_rejects_an_unknown_appliance_type(db_conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        with db_conn.transaction():
            db_conn.execute(
                "INSERT INTO user_appliance (user_id, appliance_type, input_brand, "
                "input_model, match_kind, matched_at) "
                "VALUES ('u', 'fridge', 'b', 'm', 'none', now())"
            )

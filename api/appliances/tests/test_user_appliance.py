from datetime import datetime, timezone

import pytest

from api.appliances.eprel_sync import load_config, save_rows, to_row
from api.appliances.match import (
    add_appliance,
    list_user_appliances,
    main,
    remove_appliance,
)

FETCHED = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
CONFIG = load_config()
USER = "test_user_appliance"
BASE = 9_000_000_000   # far above EPREL's registration numbers


@pytest.fixture
def tv(db_conn):
    """One made-up TV in the catalogue."""
    record = {
        "eprelRegistrationNumber": str(BASE + 1),
        "modelIdentifier": "QX55-T7",
        "supplierOrTrademark": "Zyxlo",
        "energyClass": "F",
    }
    mapping = CONFIG["types"]["ecra"]["groups"]["electronicdisplays"]
    save_rows(db_conn, [to_row(record, "electronicdisplays", "ecra", mapping, FETCHED)])
    return BASE + 1


def test_two_tvs_can_be_added(db_conn, tv):
    first, _ = add_appliance(db_conn, USER, "ecra", "Zyxlo", "QX55T7")
    second, match = add_appliance(db_conn, USER, "ecra", "Zyxlo", "QX55T7")

    assert first != second
    assert match.match_kind == "exact"
    rows = list_user_appliances(db_conn, USER)
    assert [row["id"] for row in rows] == [first, second]
    assert rows[0]["registration_number"] == tv
    assert rows[0]["model_identifier"] == "QX55-T7" and rows[0]["energy_class"] == "F"


def test_quantity_is_stored(db_conn, tv):
    add_appliance(db_conn, USER, "ecra", "Zyxlo", "QX55T7", quantity=3)

    assert list_user_appliances(db_conn, USER)[0]["quantity"] == 3


def test_quantity_below_one_is_rejected(db_conn, tv):
    with pytest.raises(ValueError, match="quantity"):
        add_appliance(db_conn, USER, "ecra", "Zyxlo", "QX55T7", quantity=0)


def test_none_match_is_stored_without_model(db_conn, tv):
    add_appliance(db_conn, USER, "ecra", "Unknownbrandxyz", "AB123")

    row = list_user_appliances(db_conn, USER)[0]
    assert row["match_kind"] == "none"
    assert row["registration_number"] is None and row["score"] is None
    assert row["input_brand"] == "Unknownbrandxyz" and row["input_model"] == "AB123"


def test_remove_appliance(db_conn, tv):
    appliance_id, _ = add_appliance(db_conn, USER, "ecra", "Zyxlo", "QX55T7")

    assert remove_appliance(db_conn, appliance_id) is True
    assert list_user_appliances(db_conn, USER) == []
    assert remove_appliance(db_conn, appliance_id) is False


def test_main_add_without_user_fails(capsys):
    assert main(["--type", "maq_lavar", "--brand", "LG", "--model", "X", "--add"]) == 2
    assert "--user" in capsys.readouterr().out

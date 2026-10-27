from collections import Counter
from datetime import datetime, timezone

import requests
import responses

from api.appliances.eprel_sync import (
    RateLimiter,
    main,
    save_rows,
    sync_group,
    to_row,
)

# Registration numbers >= 9e9: the test database is the main one, so never use real ones.
T1 = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
API = {
    "base_url": "https://eprel.test/api",
    "page_size": 100,
    "max_in_flight": 4,
    "max_per_second": 1000,
    "retries": 0,
    "timeout_s": 5,
}


def wm_mapping(config):
    return config["types"]["maq_lavar"]["groups"]["washingmachines2019"]


def test_saving_twice_updates_instead_of_duplicating(db_conn, wm_record, config):
    mapping = wm_mapping(config)
    wm_record["eprelRegistrationNumber"] = "9000000460"
    save_rows(db_conn, [to_row(wm_record, "washingmachines2019", "maq_lavar", mapping, T1)])
    wm_record["energyClass"] = "B"

    save_rows(db_conn, [to_row(wm_record, "washingmachines2019", "maq_lavar", mapping, T2)])

    rows = db_conn.execute(
        "SELECT energy_class, fetched_at, raw->>'energyClass' FROM appliance_model "
        "WHERE registration_number = 9000000460"
    ).fetchall()
    assert rows == [("B", T2, "B")]


@responses.activate
def test_sync_group_saves_and_counts(db_conn, wm_record, config):
    complete = dict(wm_record, eprelRegistrationNumber="9000000460")
    no_duration = dict(wm_record, eprelRegistrationNumber="9000000461")
    del no_duration["programmeDurationRated"]
    no_model = dict(wm_record, eprelRegistrationNumber="9000000462")
    del no_model["modelIdentifier"]
    responses.get(
        "https://eprel.test/api/products/washingmachines2019",
        json={"size": 3, "hits": [complete, no_duration, no_model]},
    )

    stats = sync_group(db_conn, requests.Session(), API, "k", "washingmachines2019",
                       "maq_lavar", wm_mapping(config), RateLimiter(1000))

    assert (stats["read"], stats["saved"], stats["skipped"]) == (3, 2, 1)
    assert stats["missing"] == Counter({"programme_minutes": 1})
    count = db_conn.execute(
        "SELECT count(*) FROM appliance_model "
        "WHERE registration_number IN (9000000460, 9000000461, 9000000462)"
    ).fetchone()[0]
    assert count == 2


def test_main_without_key_fails(monkeypatch, capsys):
    monkeypatch.delenv("EPREL_API_KEY", raising=False)
    monkeypatch.setattr("api.appliances.eprel_sync.load_dotenv", lambda: None)

    assert main([]) == 1
    assert "EPREL_API_KEY" in capsys.readouterr().out


def test_main_rejects_an_unknown_group(capsys):
    assert main(["--group", "lightsources"]) == 2
    assert "washingmachines2019" in capsys.readouterr().out


def test_main_rejects_an_unknown_type(capsys):
    assert main(["--type", "fridge"]) == 2
    assert "frigorifico" in capsys.readouterr().out

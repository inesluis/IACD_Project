"""A bad record or a database error must not stop the whole sync (no database needed)."""

import psycopg
import responses
import requests

from api.appliances import eprel_sync
from api.appliances.eprel_sync import RateLimiter, sync_group

API = {"base_url": "https://eprel.test/api", "page_size": 100, "max_in_flight": 4,
       "max_per_second": 1000, "retries": 0, "timeout_s": 5}
URL = "https://eprel.test/api/products/washingmachines2019"


def mapping(config):
    return config["types"]["maq_lavar"]["groups"]["washingmachines2019"]


@responses.activate
def test_records_that_are_not_objects_are_skipped(config):
    responses.get(URL, json={"size": 2, "hits": [None, "oops"]})

    stats = sync_group(None, requests.Session(), API, "k", "washingmachines2019",
                       "maq_lavar", mapping(config), RateLimiter(1000))

    assert (stats["read"], stats["saved"], stats["skipped"]) == (2, 0, 2)


@responses.activate
def test_a_row_the_database_refuses_is_skipped_and_the_rest_saved(monkeypatch, wm_record, config):
    good = dict(wm_record, eprelRegistrationNumber="9000000001")
    bad = dict(wm_record, eprelRegistrationNumber="9000000002")
    responses.get(URL, json={"size": 2, "hits": [good, bad]})
    saved = []

    def fake_save(conn, rows):
        if any(row[0] == 9000000002 for row in rows):
            raise psycopg.DataError("refused")
        saved.extend(row[0] for row in rows)
        return len(rows)

    monkeypatch.setattr(eprel_sync, "save_rows", fake_save)

    stats = sync_group(None, requests.Session(), API, "k", "washingmachines2019",
                       "maq_lavar", mapping(config), RateLimiter(1000))

    assert saved == [9000000001]
    assert (stats["saved"], stats["skipped"]) == (1, 1)


def test_main_continues_after_a_database_error(monkeypatch, capsys):
    class FakeConn:
        def close(self):
            pass

    calls = []

    def fake_sync(conn, session, api, key, group, *rest):
        calls.append(group)
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setenv("EPREL_API_KEY", "k")
    monkeypatch.setattr(eprel_sync, "load_dotenv", lambda: None)
    monkeypatch.setattr(eprel_sync, "open_database", lambda *a: FakeConn())
    monkeypatch.setattr(eprel_sync, "sync_group", fake_sync)

    assert eprel_sync.main(["--type", "maq_lavar"]) == 1
    assert calls == ["washingmachines2019", "washingmachines"]
    assert "FAILED" in capsys.readouterr().out

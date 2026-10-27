"""Download the EPREL appliance catalogue into the appliance_model table.

Run from the repository root:

    python -m api.appliances.eprel_sync                   # all groups
    python -m api.appliances.eprel_sync --type maq_lavar  # one appliance type
    python -m api.appliances.eprel_sync --group ovens     # one EPREL group

The types, groups and field mapping are in configs/eprel.yaml.
The API key is read from EPREL_API_KEY (put it in .env).
"""

import argparse
import math
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import requests
import yaml
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from api.appliances.match import normalize_brand, normalize_model
from api.weather.db import open_database, write_rows

CONFIG_DIR = Path(__file__).parent / "configs"

# Columns of appliance_model, in the order rows are built.
MODEL_COLUMNS = [
    "registration_number", "product_group", "appliance_type",
    "brand", "brand_key", "model_identifier", "model_key", "energy_class",
    "energy_kwh_cycle", "programme_minutes", "energy_kwh_annual", "power_on_w",
    "capacity", "capacity_unit", "on_market_start", "on_market_end",
    "raw", "fetched_at",
]


def load_config(path=CONFIG_DIR / "eprel.yaml"):
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


APPLIANCE_TYPES = tuple(load_config()["types"])

# Fields every record must have; a record without one is skipped.
REQUIRED_FIELDS = ("eprelRegistrationNumber", "modelIdentifier", "supplierOrTrademark")

# Typed columns that a group's mapping can fill, and how to convert them.
TYPED_COLUMNS = {
    "energy_kwh_cycle": float,
    "programme_minutes": int,
    "energy_kwh_annual": float,
    "power_on_w": float,
    "capacity": float,
}


# ---------------------------------------------------------------- Transform

_STEP = re.compile(r"([^.\[\]]+)(?:\[(\d+)\])?")


def get_path(record, path):
    """Value at ``path`` ("a.b", "a[0].b") in ``record``, or None if any part is missing.

    With a list of paths, the first value that is not None.
    """
    if isinstance(path, list):
        for one in path:
            value = get_path(record, one)
            if value is not None:
                return value
        return None

    value = record
    for part in path.split("."):
        match = _STEP.fullmatch(part)
        if match is None or not isinstance(value, dict):
            return None
        value = value.get(match.group(1))
        if match.group(2) is not None:
            index = int(match.group(2))
            if not isinstance(value, list) or index >= len(value):
                return None
            value = value[index]
        if value is None:
            return None
    return value


INT_MAX = 2**31 - 1   # largest value of a PostgreSQL integer column


def _number(value, kind):
    """``value`` as float or int, or None when it is not a usable number.

    Negative, infinite and NaN values give None: the schema only accepts >= 0.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    if kind is int and number > INT_MAX:
        return None
    return kind(number)


def _date(value):
    """[y, m, d] -> date. None for anything else, and for EPREL's "no end" years (9999)."""
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        day = date(*(int(part) for part in value))
    except (TypeError, ValueError):
        return None
    return None if day.year > 9000 else day


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def missing_required(record):
    """The first required field that is absent or blank, or None."""
    for field in REQUIRED_FIELDS:
        value = record.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return field
    return None


def missing_fields(record, mapping):
    """Typed columns of ``mapping`` that have no value in ``record``."""
    return [
        column for column, kind in TYPED_COLUMNS.items()
        if column in mapping and _number(get_path(record, mapping[column]), kind) is None
    ]


def to_row(record, group, appliance_type, mapping, fetched_at):
    """One appliance_model row (a tuple in MODEL_COLUMNS order) from an EPREL record."""
    brand = str(record["supplierOrTrademark"]).strip()
    model = str(record["modelIdentifier"]).strip()
    row = {
        "registration_number": int(record["eprelRegistrationNumber"]),
        "product_group": group,
        "appliance_type": appliance_type,
        "brand": brand,
        "brand_key": normalize_brand(brand),
        "model_identifier": model,
        "model_key": normalize_model(model),
        "energy_class": _text(record.get("energyClass")),
        "capacity_unit": mapping.get("capacity_unit"),
        "on_market_start": _date(record.get("onMarketStartDate")),
        "on_market_end": _date(record.get("onMarketEndDate")),
        "raw": Jsonb(record),
        "fetched_at": fetched_at,
    }
    for column, kind in TYPED_COLUMNS.items():
        path = mapping.get(column)
        row[column] = _number(get_path(record, path), kind) if path else None
    if row["capacity"] is None:
        row["capacity_unit"] = None
    return tuple(row[column] for column in MODEL_COLUMNS)


# ---------------------------------------------------------------- Extract

class EprelAuthError(Exception):
    """EPREL refused the API key."""


class RateLimiter:
    """Spaces calls to wait() at least 1/per_second apart, across threads."""

    def __init__(self, per_second, clock=time.monotonic, sleep=time.sleep):
        self.interval = 1.0 / per_second
        self.clock = clock
        self.sleep = sleep
        self.next_start = None
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = self.clock()
            start = now if self.next_start is None else max(now, self.next_start)
            self.next_start = start + self.interval
        if start > now:
            self.sleep(start - now)


def fetch_page(session, api, api_key, group, page, sleep=time.sleep, before_request=None):
    """One page of a group: {"size": ..., "hits": [...]}.

    Retries on 429, 5xx, connection errors, cut-off bodies and invalid JSON,
    waiting 2, 4, 8, ... s. ``before_request`` runs before every attempt
    (the rate limiter), so retries count towards the limit too.
    """
    url = f"{api['base_url']}/products/{group}"
    headers = {"X-API-KEY": api_key, "Accept": "application/json"}
    params = {"_page": page, "_limit": api["page_size"]}
    error = None
    for attempt in range(api["retries"] + 1):
        if before_request:
            before_request()
        try:
            response = session.get(url, headers=headers, params=params,
                                   timeout=api["timeout_s"])
            if response.status_code in (401, 403):
                raise EprelAuthError(
                    f"EPREL refused the key (HTTP {response.status_code}): "
                    "check EPREL_API_KEY in .env"
                )
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response.json()
            error = requests.HTTPError(
                f"HTTP {response.status_code} for {group} page {page}", response=response
            )
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError,
                ValueError) as exc:   # ValueError: the body is not valid JSON
            error = exc
        if attempt < api["retries"]:
            sleep(2 ** (attempt + 1))
    raise error


def iter_group_pages(session, api, api_key, group, limiter, sleep=time.sleep):
    """Yield the records of every page of ``group``, a list per page.

    Page 1 gives the total size; the other pages run up to max_in_flight at a
    time, each started through ``limiter``. Pages come in completion order.
    """
    def fetch(page):
        return fetch_page(session, api, api_key, group, page, sleep=sleep,
                          before_request=limiter.wait)

    first = fetch(1)
    if not first.get("hits"):
        return
    yield first["hits"]

    pages = math.ceil(first.get("size", 0) / api["page_size"])
    pool = ThreadPoolExecutor(max_workers=api["max_in_flight"])
    try:
        # No list of futures is kept: as_completed lets go of each finished
        # page, so a large group is not held in memory all at once.
        for future in as_completed([pool.submit(fetch, page) for page in range(2, pages + 1)]):
            records = future.result().get("hits") or []
            if records:
                yield records
    finally:
        # On an error, do not wait for the pages not started yet.
        pool.shutdown(wait=True, cancel_futures=True)


# ---------------------------------------------------------------- Load

def save_rows(conn, rows):
    """Insert or update appliance_model rows. Returns how many were written."""
    return write_rows(conn, "appliance_model", MODEL_COLUMNS, rows,
                      conflict=("registration_number",))


def save_page(conn, rows):
    """Save one page; if the database refuses it, save row by row.

    Returns (saved, refused): a bad row costs only itself, not the page.
    """
    try:
        return save_rows(conn, rows), 0
    except (psycopg.DataError, psycopg.IntegrityError):
        saved = refused = 0
        for row in rows:
            try:
                saved += save_rows(conn, [row])
            except (psycopg.DataError, psycopg.IntegrityError):
                refused += 1
        return saved, refused


def sync_group(conn, session, api, api_key, group, appliance_type, mapping, limiter):
    """Download one group and save it page by page. Returns the counts."""
    fetched_at = datetime.now(timezone.utc)
    stats = {"read": 0, "saved": 0, "skipped": 0, "missing": Counter()}
    for records in iter_group_pages(session, api, api_key, group, limiter):
        rows = []
        for record in records:
            stats["read"] += 1
            # Validate: a record that is not an object, or lacks its identity
            # fields, cannot be stored.
            if not isinstance(record, dict) or missing_required(record):
                stats["skipped"] += 1
                continue
            try:
                row = to_row(record, group, appliance_type, mapping, fetched_at)
            except (TypeError, ValueError):   # e.g. a registration number that is not a number
                stats["skipped"] += 1
                continue
            stats["missing"].update(missing_fields(record, mapping))
            rows.append(row)
        if rows:
            saved, refused = save_page(conn, rows)
            stats["saved"] += saved
            stats["skipped"] += refused
    return stats


# ---------------------------------------------------------------- Pipeline

def select_groups(config, groups=None, types=None):
    """(group, appliance_type, mapping) for the chosen groups/types, or all.

    Raises ValueError naming the valid choices when a name is unknown.
    """
    everything = [
        (group, appliance_type, mapping)
        for appliance_type, spec in config["types"].items()
        for group, mapping in spec["groups"].items()
    ]
    known_groups = [group for group, _, _ in everything]
    unknown = [g for g in groups or [] if g not in known_groups]
    unknown += [t for t in types or [] if t not in config["types"]]
    if unknown:
        raise ValueError(
            f"unknown name: {', '.join(unknown)}\n"
            f"  types: {', '.join(config['types'])}\n"
            f"  groups: {', '.join(known_groups)}"
        )
    if not groups and not types:
        return everything
    return [
        item for item in everything
        if item[0] in (groups or []) or item[1] in (types or [])
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download the EPREL appliance catalogue.")
    parser.add_argument("--group", action="append", help="an EPREL group, e.g. ovens (repeatable)")
    parser.add_argument("--type", action="append", help="an appliance type, e.g. maq_lavar (repeatable)")
    args = parser.parse_args(argv)

    config = load_config()
    try:
        chosen = select_groups(config, args.group, args.type)
    except ValueError as error:
        print(f"FAILED - {error}")
        return 2

    load_dotenv()
    api_key = os.environ.get("EPREL_API_KEY")
    if not api_key:
        print("FAILED - EPREL_API_KEY is not set: add it to .env")
        return 1

    conn = open_database("appliance_model", MODEL_COLUMNS)
    if conn is None:
        return 1

    api = config["api"]
    session = requests.Session()
    limiter = RateLimiter(api["max_per_second"])
    status = 0
    try:
        for group, appliance_type, mapping in chosen:
            try:
                stats = sync_group(conn, session, api, api_key, group,
                                   appliance_type, mapping, limiter)
            except EprelAuthError as error:
                print(f"FAILED - {error}")
                return 1
            except (requests.RequestException, psycopg.Error) as error:
                print(f"{group}: FAILED - {error} (saved pages are kept; run it again)")
                status = 1
                continue
            print(f"{group}: read {stats['read']}, saved {stats['saved']}, "
                  f"skipped {stats['skipped']}")
            if stats["missing"]:
                missing = ", ".join(f"{field}={count}" for field, count in stats["missing"].items())
                print(f"  missing: {missing}")
    finally:
        conn.close()
    return status


if __name__ == "__main__":
    sys.exit(main())

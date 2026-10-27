"""Find the catalogue model closest to what a user says they own.

    python -m api.appliances.match --type maq_lavar --brand "LG Electronics" --model F4WV308S4
    python -m api.appliances.match --user demo_coimbra --type ecra --brand Samsung --model UE55TU7172 --add

The result says how far to trust it:
  exact  same model, ignoring case, spaces and punctuation
  close  similar name (score >= 0.8), e.g. a missing suffix
  weak   the most similar model of that brand, but not very similar
  none   the brand has no models of that type in the catalogue
"""

import argparse
import difflib
import re
import sys
import unicodedata
from dataclasses import dataclass, field

from dotenv import load_dotenv
from psycopg.rows import dict_row

from api.weather.db import open_database

# ---------------------------------------------------------------- Names
# Normalised brand and model names, so different spellings compare equal.
# eprel_sync.py uses them too, to fill brand_key and model_key.

# Company words dropped from the end of a brand: "LG Electronics Inc." -> "lg electronics".
COMPANY_WORDS = frozenset(
    "inc ltd gmbh sa sl srl spa ag bv co corp corporation limited".split()
)


def normalize_model(text):
    """Uppercase letters and digits only: "F4WV-308 s4w" -> "F4WV308S4W"."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def normalize_brand(text):
    """Lowercase, no accents or punctuation, no trailing company words."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    # Dots are dropped, not turned into spaces, so "S.A." becomes "sa".
    words = re.sub(r"[^a-z0-9]+", " ", text.lower().replace(".", "")).split()
    # Keep at least one word, so a brand called "CO" stays "co".
    while len(words) > 1 and words[-1] in COMPANY_WORDS:
        words.pop()
    return " ".join(words)


# ---------------------------------------------------------------- Matching

CLOSE_SCORE = 0.8   # at or above: "close", below: "weak"
PREFIX_MIN = 5      # shortest key that earns the prefix bonus
PREFIX_SCORE = 0.9  # minimum score when one key starts with the other

MODEL_FIELDS = ("registration_number", "product_group", "brand", "model_identifier",
                "energy_class")


@dataclass
class Match:
    match_kind: str                 # exact, close, weak or none
    score: float | None             # 0 to 1; None when nothing matched
    model: dict | None              # the chosen model, with its score
    candidates: list[dict] = field(default_factory=list)       # the next best models
    brand_suggestions: list[str] = field(default_factory=list)  # when the brand is unknown


def score_model(wanted, candidate):
    """Similarity of two normalised model names, from 0 to 1."""
    if wanted == candidate:
        return 1.0
    score = difflib.SequenceMatcher(None, wanted, candidate).ratio()
    shorter, longer = sorted((wanted, candidate), key=len)
    if len(shorter) >= PREFIX_MIN and longer.startswith(shorter):
        score = max(score, PREFIX_SCORE)
    return score


def _brand_names(conn, appliance_type):
    """{brand_key: display name}, the display name being the most common spelling."""
    rows = conn.execute(
        "SELECT brand_key, brand, count(*) AS n FROM appliance_model "
        "WHERE appliance_type = %s GROUP BY brand_key, brand "
        "ORDER BY brand_key, n DESC, brand",
        (appliance_type,),
    ).fetchall()
    names = {}
    for brand_key, brand, _ in rows:
        names.setdefault(brand_key, brand)
    return names


def suggest_brands(brand_key, names, limit=5):
    """Display names of the brands closest to ``brand_key``.

    ``names`` is {brand_key: display name}. Brands that start with the typed
    words come first ("lg" -> "LG Electronics"), then similar spellings.
    """
    starting = sorted(key for key in names if key.startswith(brand_key + " "))
    similar = difflib.get_close_matches(brand_key, list(names), n=limit, cutoff=0.6)
    keys = list(dict.fromkeys(starting + similar))[:limit]
    return [names[key] for key in keys]


def find_match(conn, appliance_type, brand, model, config=None):
    """The catalogue model of ``appliance_type`` and ``brand`` closest to ``model``."""
    if config is None:
        # Imported here, not at the top: eprel_sync imports this module for the names.
        from api.appliances.eprel_sync import load_config
        config = load_config()
    if appliance_type not in config["types"]:
        raise ValueError(f"unknown appliance type: {appliance_type} "
                         f"(choose from {', '.join(config['types'])})")
    model_key = normalize_model(model)
    if not model_key:
        raise ValueError("model is empty")
    brand_key = normalize_brand(brand)
    if not brand_key:
        raise ValueError("brand is empty")

    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT registration_number, product_group, brand, model_identifier, "
            "model_key, energy_class, on_market_start FROM appliance_model "
            "WHERE appliance_type = %s AND brand_key = %s",
            (appliance_type, brand_key),
        ).fetchall()

    if not rows:
        suggestions = suggest_brands(brand_key, _brand_names(conn, appliance_type))
        return Match("none", None, None, [], suggestions)

    # Newer regulation first (config order), then the latest on the market.
    rank = {group: i for i, group in enumerate(config["types"][appliance_type]["groups"])}
    for row in rows:
        row["score"] = score_model(model_key, row["model_key"])
    rows.sort(key=lambda row: (
        -row["score"],
        rank.get(row["product_group"], len(rank)),
        -(row["on_market_start"].toordinal() if row["on_market_start"] else 0),
    ))
    found = [{name: row[name] for name in (*MODEL_FIELDS, "score")} for row in rows[:5]]
    best = found[0]["score"]
    kind = "exact" if best == 1.0 else "close" if best >= CLOSE_SCORE else "weak"
    return Match(kind, best, found[0], found[1:])


def list_brands(conn, appliance_type):
    """The brands with models of ``appliance_type``, one name per brand, sorted."""
    return sorted(_brand_names(conn, appliance_type).values(), key=str.casefold)


def list_models(conn, appliance_type, brand):
    """The models of ``brand`` and ``appliance_type``, with their main figures."""
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            "SELECT registration_number, model_identifier, energy_class, energy_kwh_cycle, "
            "programme_minutes, energy_kwh_annual, power_on_w, capacity, capacity_unit "
            "FROM appliance_model WHERE appliance_type = %s AND brand_key = %s "
            "ORDER BY model_identifier",
            (appliance_type, normalize_brand(brand)),
        ).fetchall()


# ---------------------------------------------------------------- User appliances

def add_appliance(conn, user_id, appliance_type, brand, model, quantity=1):
    """Match ``brand``/``model`` and store it for ``user_id``. Returns (id, Match)."""
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    match = find_match(conn, appliance_type, brand, model)
    registration = match.model["registration_number"] if match.model else None
    with conn.transaction():
        appliance_id = conn.execute(
            "INSERT INTO user_appliance (user_id, appliance_type, input_brand, input_model, "
            "registration_number, match_kind, score, quantity, matched_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id",
            (user_id, appliance_type, brand, model, registration,
             match.match_kind, match.score, quantity),
        ).fetchone()[0]
    return appliance_id, match


def remove_appliance(conn, appliance_id):
    """Delete one user appliance. True if it existed."""
    with conn.transaction():
        cursor = conn.execute("DELETE FROM user_appliance WHERE id = %s", (appliance_id,))
    return cursor.rowcount == 1


def list_user_appliances(conn, user_id):
    """The appliances of ``user_id`` with their matched model, by type."""
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            "SELECT u.id, u.appliance_type, u.input_brand, u.input_model, "
            "u.registration_number, u.match_kind, u.score, u.quantity, u.matched_at, "
            "m.brand, m.model_identifier, m.energy_class "
            "FROM user_appliance u "
            "LEFT JOIN appliance_model m USING (registration_number) "
            "WHERE u.user_id = %s ORDER BY u.appliance_type, u.id",
            (user_id,),
        ).fetchall()


# ---------------------------------------------------------------- Command

def _describe(model):
    return (f"{model['brand']} {model['model_identifier']} "
            f"[{model['product_group']}, reg {model['registration_number']}, "
            f"class {model['energy_class'] or '?'}]")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Find the catalogue model closest to an appliance.")
    parser.add_argument("--type", required=True, help="appliance type, e.g. maq_lavar")
    parser.add_argument("--brand", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--user", help="user id, needed with --add")
    parser.add_argument("--add", action="store_true", help="store the match for the user")
    parser.add_argument("--quantity", type=int, default=1)
    args = parser.parse_args(argv)

    if args.add and not args.user:
        print("FAILED - --add needs --user")
        return 2

    load_dotenv()
    conn = open_database("user_appliance", ["registration_number", "match_kind"])
    if conn is None:
        return 1
    try:
        if args.add:
            appliance_id, match = add_appliance(conn, args.user, args.type, args.brand,
                                                args.model, args.quantity)
        else:
            match = find_match(conn, args.type, args.brand, args.model)
    except ValueError as error:
        print(f"FAILED - {error}")
        return 2
    finally:
        conn.close()

    if match.model is None:
        print(f"none: no {args.type} models of brand {args.brand!r} in the catalogue")
        if match.brand_suggestions:
            print(f"did you mean: {', '.join(match.brand_suggestions)}")
    else:
        print(f"{match.match_kind} ({match.score:.2f}): {_describe(match.model)}")
        if match.candidates:
            print("other candidates:")
            for candidate in match.candidates:
                print(f"  {candidate['score']:.2f}  {candidate['model_identifier']}  "
                      f"[{candidate['product_group']}, reg {candidate['registration_number']}]")
    if args.add:
        print(f"saved for {args.user} (id {appliance_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

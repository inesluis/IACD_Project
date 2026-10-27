"""PostgreSQL helpers shared by the weather pipelines.

The connection string comes from the environment variable DATABASE_URL.
The pipelines call load_dotenv() first, so it can live in the .env file.
"""

import os
from pathlib import Path

import psycopg
from psycopg import sql

SCHEMA_FILE = Path(__file__).parents[2] / "database" / "schema.sql"


def connect(url=None):
    """Open a connection to ``url``, or to DATABASE_URL when no url is given."""
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError(
            "DATABASE_URL is not set: copy .env.example to .env and fill it in"
        )
    # Autocommit: a plain query never leaves a transaction open, so every
    # "with conn.transaction():" block is a real transaction that commits.
    return psycopg.connect(url, autocommit=True)


def ensure_schema(conn):
    """Create the tables that do not exist yet. Safe to run every time."""
    # Commits on its own, or becomes a savepoint inside a caller's transaction
    # (the tests run inside one that is always rolled back).
    with conn.transaction():
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


def check_columns(conn, table, names):
    """Raise ValueError listing every name in ``names`` that is not a column of ``table``.

    Catches a variable added to the YAML config but not to database/schema.sql.
    """
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    ).fetchall()
    columns = {row[0] for row in rows}
    missing = [name for name in names if name not in columns]
    if missing:
        raise ValueError(
            f"columns missing from table {table}: {', '.join(missing)} "
            "(add them to database/schema.sql)"
        )


def open_database(table, columns):
    """Connect, create missing tables and check ``columns`` exist in ``table``.

    Returns the connection, or None after printing one line saying why the
    database cannot be used.
    """
    try:
        conn = connect()
    except ValueError as error:
        print(f"FAILED - {error}")
        return None
    except psycopg.OperationalError as error:
        reason = str(error).strip().splitlines()[0]
        print(f"FAILED - cannot connect to the database in DATABASE_URL ({reason}). "
              "Is it running? (docker compose up -d db)")
        return None

    try:
        ensure_schema(conn)
        check_columns(conn, table, columns)
    except (ValueError, psycopg.Error) as error:
        print(f"FAILED - {error}")
        conn.close()
        return None
    return conn


def write_rows(conn, table, columns, rows, conflict=None):
    """Insert ``rows`` (tuples in the order of ``columns``) in one transaction.

    With ``conflict`` (the key columns, e.g. ("user_id", "time")) a row whose
    key is already stored is overwritten instead. Returns the number of rows.
    """
    query = sql.SQL("INSERT INTO {table} ({columns}) VALUES ({values})").format(
        table=sql.Identifier(table),
        columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
        values=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
    )
    if conflict:
        updates = [name for name in columns if name not in conflict]
        query += sql.SQL(" ON CONFLICT ({keys}) DO UPDATE SET {updates}").format(
            keys=sql.SQL(", ").join(map(sql.Identifier, conflict)),
            updates=sql.SQL(", ").join(
                sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(name))
                for name in updates
            ),
        )

    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.executemany(query, rows)
    return len(rows)

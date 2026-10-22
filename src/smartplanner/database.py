"""Short-lived PostgreSQL connections with explicit transaction boundaries."""

import psycopg
from psycopg.rows import dict_row

from .sqlite_store import connect_sqlite


def connect(database_url: str):
    if database_url.startswith("sqlite:///"):
        return connect_sqlite(database_url)
    return psycopg.connect(
        database_url, autocommit=True, row_factory=dict_row, connect_timeout=5,
        options="-c timezone=UTC -c statement_timeout=10000 -c lock_timeout=3000",
    )

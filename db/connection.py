"""MySQL connection helpers for read-only explorer endpoints."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pymysql


def _read_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_db_connection(database: str | None = None) -> pymysql.connections.Connection:
    """Return a direct pymysql connection using server-local DB settings."""

    selected_database = (database or '').strip() or _read_required_env("DB_NAME")

    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=_read_required_env("DB_USER"),
        password=_read_required_env("DB_PASSWORD"),
        database=selected_database,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )


@contextmanager
def db_cursor(database: str | None = None):
    """Yield a dict cursor and close connection cleanly."""
    conn = get_db_connection(database=database)
    try:
        with conn.cursor() as cursor:
            yield cursor
    finally:
        conn.close()


def _serialize_cell(value: Any) -> Any:
    """Convert a single pymysql cell value to a JSON-safe type."""
    if isinstance(value, timedelta):
        # MySQL TIME columns come back as timedelta; convert to HH:MM:SS string.
        total_seconds = int(value.total_seconds())
        sign = "-" if total_seconds < 0 else ""
        total_seconds = abs(total_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def serialize_db_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize a list of pymysql DictCursor rows to JSON-safe dicts."""
    return [{k: _serialize_cell(v) for k, v in row.items()} for row in rows]

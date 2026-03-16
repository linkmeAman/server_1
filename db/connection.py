"""MySQL connection helpers for read-only explorer endpoints."""

from __future__ import annotations

import os
from contextlib import contextmanager

import pymysql


def _read_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_db_connection() -> pymysql.connections.Connection:
    """Return a direct pymysql connection using server-local DB settings."""

    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=_read_required_env("DB_USER"),
        password=_read_required_env("DB_PASSWORD"),
        database=_read_required_env("DB_NAME"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )


@contextmanager
def db_cursor():
    """Yield a dict cursor and close connection cleanly."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            yield cursor
    finally:
        conn.close()

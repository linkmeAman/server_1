"""Shared security helpers for DB Explorer routes."""

from __future__ import annotations

import os
import re

from fastapi import HTTPException

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_BLOCKED_DATABASES = {
    "information_schema",
    "mysql",
    "performance_schema",
    "sys",
}


def _parse_csv_set(env_name: str) -> set[str]:
    raw = os.getenv(env_name, "")
    values: set[str] = set()
    for item in raw.split(","):
        cleaned = item.strip().lower()
        if cleaned:
            values.add(cleaned)
    return values


def validate_identifier(name: str, field_name: str) -> str:
    value = str(name or "").strip()
    if not _IDENTIFIER_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return value


def _blocked_databases() -> set[str]:
    configured = _parse_csv_set("DB_EXPLORER_BLOCKED_DATABASES")
    if configured:
        return configured
    return set(_DEFAULT_BLOCKED_DATABASES)


def _allowed_databases() -> set[str]:
    return _parse_csv_set("DB_EXPLORER_ALLOWED_DATABASES")


def normalize_database_name(db: str | None) -> str | None:
    if db is None:
        return None

    cleaned = db.strip()
    if not cleaned:
        return None

    safe_db = validate_identifier(cleaned, "database name")
    safe_db_lower = safe_db.lower()

    if safe_db_lower in _blocked_databases():
        raise HTTPException(status_code=403, detail=f"Database '{safe_db}' is not accessible")

    allowed = _allowed_databases()
    if allowed and safe_db_lower not in allowed:
        raise HTTPException(status_code=403, detail=f"Database '{safe_db}' is not in the allowed list")

    return safe_db


def filter_database_list(names: list[str]) -> list[str]:
    blocked = _blocked_databases()
    allowed = _allowed_databases()
    visible: list[str] = []

    for name in names:
        safe_name = validate_identifier(name, "database name")
        lowered = safe_name.lower()

        if lowered in blocked:
            continue
        if allowed and lowered not in allowed:
            continue

        visible.append(safe_name)

    return visible

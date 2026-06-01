"""Helpers for selecting Alembic's database target."""

from __future__ import annotations

import os
from typing import Mapping

VALID_DB_TARGETS = {"main", "central"}
DEFAULT_DB_TARGET = "central"


def _get_main_async_engine():
    from app.core.database import get_main_async_engine

    return get_main_async_engine()


def _get_central_async_engine():
    from app.core.database import get_central_async_engine

    return get_central_async_engine()


def resolve_database_target(x_args: Mapping[str, str] | None = None) -> str:
    raw_target = (
        (x_args or {}).get("db_target")
        or (x_args or {}).get("database")
        or os.getenv("ALEMBIC_DB_TARGET")
        or DEFAULT_DB_TARGET
    )
    target = raw_target.strip().lower()
    if target not in VALID_DB_TARGETS:
        raise RuntimeError(
            f"Unsupported Alembic db target '{raw_target}'. Use 'main' or 'central'."
        )
    return target


def resolve_database_url(x_args: Mapping[str, str] | None = None) -> str:
    target = resolve_database_target(x_args)
    engine = _get_main_async_engine() if target == "main" else _get_central_async_engine()
    return engine.url.render_as_string(hide_password=False)

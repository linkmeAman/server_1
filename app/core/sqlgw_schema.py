"""Schema introspection service for SQL Gateway policy management."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Tuple

from sqlalchemy import inspect, text

from app.core.database import engines
from app.core.settings import get_settings

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SQLGWSchemaError(Exception):
    """Schema service error."""

    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


_schema_cache_lock = threading.Lock()
_schema_cache: Dict[Tuple[str, str, str], Tuple[float, Any]] = {}


def clear_schema_cache() -> None:
    with _schema_cache_lock:
        _schema_cache.clear()


def _cache_get(cache_key: Tuple[str, str, str], ttl_seconds: int):
    now = time.time()
    with _schema_cache_lock:
        item = _schema_cache.get(cache_key)
        if item is None:
            return None
        expires_at, payload = item
        if now > expires_at:
            _schema_cache.pop(cache_key, None)
            return None
        return payload


def _cache_set(cache_key: Tuple[str, str, str], ttl_seconds: int, payload: Any) -> None:
    expires_at = time.time() + max(int(ttl_seconds), 1)
    with _schema_cache_lock:
        _schema_cache[cache_key] = (expires_at, payload)


def list_supported_databases() -> List[str]:
    settings = get_settings()
    db_map = getattr(settings, "SQL_GATEWAY_DB_ENGINE_MAP", {})
    if not isinstance(db_map, dict) or db_map.get("__invalid__") is True:
        raise SQLGWSchemaError("SQLGW_CONFIG_INVALID", "SQL gateway DB map config is invalid", 503)

    aliases = [str(alias) for alias in db_map.keys() if alias and alias != "__invalid__"]
    aliases = sorted(set(aliases))
    if not aliases:
        raise SQLGWSchemaError("SQLGW_CONFIG_INVALID", "No SQL gateway DB aliases configured", 503)
    return aliases


def _validate_identifier(name: str) -> None:
    if not isinstance(name, str) or not IDENTIFIER_PATTERN.match(name) or name == "*":
        raise SQLGWSchemaError("SQLGW_INVALID_IDENTIFIER", f"Invalid identifier '{name}'", 400)


def _resolve_engine(db_alias: str):
    if not isinstance(db_alias, str) or not db_alias:
        raise SQLGWSchemaError("SQLGW_INVALID_OPERATOR_PAYLOAD", "db alias is required", 400)

    settings = get_settings()
    db_map = getattr(settings, "SQL_GATEWAY_DB_ENGINE_MAP", {})
    if not isinstance(db_map, dict) or db_map.get("__invalid__") is True:
        raise SQLGWSchemaError("SQLGW_CONFIG_INVALID", "SQL gateway DB map config is invalid", 503)

    if db_alias not in db_map:
        raise SQLGWSchemaError("SQLGW_FORBIDDEN_TABLE", f"Unsupported database alias '{db_alias}'", 403)

    engine_key = db_map.get(db_alias)
    if not isinstance(engine_key, str) or not engine_key:
        raise SQLGWSchemaError("SQLGW_CONFIG_INVALID", f"Invalid engine mapping for '{db_alias}'", 503)

    engine = engines.get(engine_key)
    if engine is None:
        raise SQLGWSchemaError("SQLGW_CONFIG_INVALID", f"Engine '{engine_key}' is not available", 503)

    return engine


def list_tables(db_alias: str) -> List[Dict[str, Any]]:
    settings = get_settings()
    ttl = int(getattr(settings, "SQL_GATEWAY_SCHEMA_CACHE_TTL_SECONDS", 600))

    cache_key = ("tables", db_alias, "")
    cached = _cache_get(cache_key, ttl)
    if cached is not None:
        return cached

    engine = _resolve_engine(db_alias)
    dialect = engine.dialect.name

    rows: List[Dict[str, Any]] = []

    with engine.connect() as conn:
        if dialect in {"mysql", "mariadb"}:
            query = text(
                """
                SELECT table_name AS name, table_type AS table_type
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                ORDER BY table_name
                """
            )
            result = conn.execute(query).mappings().all()
            for row in result:
                table_type = str(row.get("table_type", "BASE TABLE")).upper()
                rows.append(
                    {
                        "name": row["name"],
                        "kind": "view" if "VIEW" in table_type else "table",
                    }
                )
        else:
            inspector = inspect(conn)
            for table_name in sorted(inspector.get_table_names()):
                rows.append({"name": table_name, "kind": "table"})
            for view_name in sorted(inspector.get_view_names()):
                rows.append({"name": view_name, "kind": "view"})

    _cache_set(cache_key, ttl, rows)
    return rows


def list_columns(db_alias: str, table_name: str) -> List[Dict[str, Any]]:
    _validate_identifier(table_name)

    settings = get_settings()
    ttl = int(getattr(settings, "SQL_GATEWAY_SCHEMA_CACHE_TTL_SECONDS", 600))

    cache_key = ("columns", db_alias, table_name)
    cached = _cache_get(cache_key, ttl)
    if cached is not None:
        return cached

    engine = _resolve_engine(db_alias)
    dialect = engine.dialect.name
    rows: List[Dict[str, Any]] = []

    with engine.connect() as conn:
        if dialect in {"mysql", "mariadb"}:
            query = text(
                """
                SELECT
                    c.column_name AS name,
                    c.data_type AS data_type,
                    c.is_nullable AS is_nullable,
                    CASE WHEN k.column_name IS NULL THEN 0 ELSE 1 END AS is_pk
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage k
                  ON c.table_schema = k.table_schema
                 AND c.table_name = k.table_name
                 AND c.column_name = k.column_name
                 AND k.constraint_name = 'PRIMARY'
                WHERE c.table_schema = DATABASE()
                  AND c.table_name = :table_name
                ORDER BY c.ordinal_position
                """
            )
            result = conn.execute(query, {"table_name": table_name}).mappings().all()
            for row in result:
                rows.append(
                    {
                        "name": row["name"],
                        "data_type": str(row.get("data_type") or ""),
                        "is_nullable": str(row.get("is_nullable", "YES")).upper() == "YES",
                        "is_pk": bool(row.get("is_pk")),
                    }
                )
        else:
            inspector = inspect(conn)
            columns = inspector.get_columns(table_name)
            pk_columns = set((inspector.get_pk_constraint(table_name) or {}).get("constrained_columns") or [])
            for col in columns:
                rows.append(
                    {
                        "name": col["name"],
                        "data_type": str(col.get("type") or ""),
                        "is_nullable": bool(col.get("nullable", True)),
                        "is_pk": col["name"] in pk_columns,
                    }
                )

    if not rows:
        raise SQLGWSchemaError("SQLGW_FORBIDDEN_TABLE", f"Table '{table_name}' not found", 403)

    _cache_set(cache_key, ttl, rows)
    return rows

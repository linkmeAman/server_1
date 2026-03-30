"""Policy store and lifecycle management for SQL Gateway allowlists."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import MetaData, Table, and_, desc, inspect, select, text
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import engines
from app.core.settings import get_settings
from app.core.sqlgw_schema import SQLGWSchemaError, list_columns, list_supported_databases, list_tables

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALLOWED_OPERATIONS = {"select", "insert", "update", "delete"}


class SQLGWPolicyError(Exception):
    """Policy store error."""

    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


_policy_metadata = MetaData()
_policy_table = Table(
    "sql_gateway_policy_versions",
    _policy_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("version", Integer, nullable=False, unique=True, index=True),
    Column("status", String(20), nullable=False, index=True),
    Column("policy_json", Text, nullable=False),
    Column("checksum_sha256", String(64), nullable=False),
    Column("created_by", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("approved_by", String(64), nullable=True),
    Column("approved_at", DateTime, nullable=True),
    Column("activated_by", String(64), nullable=True),
    Column("activated_at", DateTime, nullable=True),
    Column("updated_by", String(64), nullable=True),
    Column("updated_at", DateTime, nullable=True),
    Column("notes", Text, nullable=True),
)

_policy_cache_lock = threading.Lock()
_policy_cache: Dict[str, Any] = {
    "version": None,
    "policy": None,
    "loaded_at": 0.0,
}


def clear_policy_cache() -> None:
    with _policy_cache_lock:
        _policy_cache["version"] = None
        _policy_cache["policy"] = None
        _policy_cache["loaded_at"] = 0.0


def _validate_identifier(name: str) -> None:
    if not isinstance(name, str) or not IDENTIFIER_PATTERN.match(name) or name == "*":
        raise SQLGWPolicyError("SQLGW_INVALID_IDENTIFIER", f"Invalid identifier '{name}'", 400)


def _normalize_identifier_list(value: Any, field_name: str, required_non_empty: bool) -> List[str]:
    if not isinstance(value, list):
        raise SQLGWPolicyError("SQLGW_POLICY_INVALID", f"'{field_name}' must be a list", 400)

    values: List[str] = []
    for item in value:
        _validate_identifier(item)
        values.append(item)

    if required_non_empty and not values:
        raise SQLGWPolicyError("SQLGW_POLICY_INVALID", f"'{field_name}' must be non-empty", 400)

    return values


def _serialize_policy_json(policy_json: Dict[str, Any]) -> str:
    return json.dumps(policy_json, separators=(",", ":"), sort_keys=True)


def _deserialize_policy_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    raise SQLGWPolicyError("SQLGW_CONFIG_INVALID", "Stored policy_json is invalid", 503)


def _checksum(policy_json: Dict[str, Any]) -> str:
    payload = _serialize_policy_json(policy_json)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_policy_engine():
    settings = get_settings()
    db_map = getattr(settings, "SQL_GATEWAY_DB_ENGINE_MAP", {})
    if not isinstance(db_map, dict) or db_map.get("__invalid__") is True:
        raise SQLGWPolicyError("SQLGW_CONFIG_INVALID", "SQL gateway DB map config is invalid", 503)

    engine_key = db_map.get("CENTRAL")
    if not isinstance(engine_key, str) or not engine_key:
        raise SQLGWPolicyError("SQLGW_CONFIG_INVALID", "CENTRAL DB alias mapping is missing", 503)

    engine = engines.get(engine_key)
    if engine is None:
        raise SQLGWPolicyError("SQLGW_CONFIG_INVALID", f"Engine '{engine_key}' is not available", 503)

    return engine


def _ensure_policy_table() -> None:
    engine = _get_policy_engine()
    _policy_metadata.create_all(bind=engine, tables=[_policy_table], checkfirst=True)
    _ensure_policy_audit_columns(engine)


def _ensure_policy_audit_columns(engine) -> None:
    """Best-effort compatibility migration for audit fields on existing installs."""
    inspector = inspect(engine)
    existing = {str(col.get("name")) for col in inspector.get_columns("sql_gateway_policy_versions")}

    statements: List[str] = []
    if "updated_by" not in existing:
        statements.append("ALTER TABLE sql_gateway_policy_versions ADD COLUMN updated_by VARCHAR(64) NULL")
    if "updated_at" not in existing:
        statements.append("ALTER TABLE sql_gateway_policy_versions ADD COLUMN updated_at DATETIME NULL")

    if not statements:
        return

    with engine.begin() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except SQLAlchemyError as exc:
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue
                raise


def _row_to_dict(row: Any, include_policy_json: bool = False) -> Dict[str, Any]:
    mapping = dict(row._mapping)
    result = {
        "id": int(mapping["id"]),
        "version": int(mapping["version"]),
        "status": mapping["status"],
        "checksum_sha256": mapping["checksum_sha256"],
        "created_by": mapping["created_by"],
        "created_at": mapping["created_at"].isoformat() if mapping.get("created_at") else None,
        "approved_by": mapping.get("approved_by"),
        "approved_at": mapping["approved_at"].isoformat() if mapping.get("approved_at") else None,
        "activated_by": mapping.get("activated_by"),
        "activated_at": mapping["activated_at"].isoformat() if mapping.get("activated_at") else None,
        "updated_by": mapping.get("updated_by"),
        "updated_at": mapping["updated_at"].isoformat() if mapping.get("updated_at") else None,
        "notes": mapping.get("notes"),
    }
    if include_policy_json:
        result["policy_json"] = _deserialize_policy_json(mapping["policy_json"])
    return result


def list_policy_versions(limit: int = 20) -> List[Dict[str, Any]]:
    _ensure_policy_table()
    engine = _get_policy_engine()
    limit_value = max(1, min(int(limit), 100))

    with engine.connect() as conn:
        stmt = select(_policy_table).order_by(desc(_policy_table.c.version)).limit(limit_value)
        rows = conn.execute(stmt).all()
        return [_row_to_dict(row, include_policy_json=False) for row in rows]


def get_policy_version(policy_id: int) -> Dict[str, Any]:
    _ensure_policy_table()
    engine = _get_policy_engine()

    with engine.connect() as conn:
        stmt = select(_policy_table).where(_policy_table.c.id == int(policy_id)).limit(1)
        row = conn.execute(stmt).first()

    if row is None:
        raise SQLGWPolicyError("SQLGW_POLICY_NOT_FOUND", f"Policy id {policy_id} not found", 404)
    return _row_to_dict(row, include_policy_json=True)


def _validate_against_schema(policy_json: Dict[str, Any]) -> None:
    errors: List[str] = []

    tables_by_db: Dict[str, set] = {}
    for db_alias in list_supported_databases():
        try:
            tables = list_tables(db_alias)
            tables_by_db[db_alias] = {entry["name"] for entry in tables}
        except SQLGWSchemaError as exc:
            raise SQLGWPolicyError(exc.code, exc.message, exc.status_code) from exc

    for table_name, config in policy_json.items():
        db_alias = config.get("db")
        if db_alias not in tables_by_db:
            errors.append(f"Unknown db alias '{db_alias}' for table '{table_name}'")
            continue

        if table_name not in tables_by_db[db_alias]:
            errors.append(f"Missing table '{table_name}' in DB '{db_alias}'")
            continue

        try:
            columns = list_columns(db_alias, table_name)
        except SQLGWSchemaError as exc:
            errors.append(f"Could not introspect '{db_alias}.{table_name}': {exc.message}")
            continue

        actual_columns = {col["name"] for col in columns}
        for field in [
            "select_columns",
            "filter_columns",
            "group_columns",
            "order_columns",
            "insert_columns",
            "update_columns",
        ]:
            for col_name in config.get(field, []):
                if col_name not in actual_columns:
                    errors.append(
                        f"Missing column '{col_name}' in '{db_alias}.{table_name}' for '{field}'"
                    )

    if errors:
        raise SQLGWPolicyError("SQLGW_POLICY_INVALID", "; ".join(errors), 400)


def validate_policy_json(policy_json: Dict[str, Any], validate_schema: bool = False) -> Dict[str, Any]:
    if not isinstance(policy_json, dict) or not policy_json:
        raise SQLGWPolicyError("SQLGW_POLICY_INVALID", "policy_json must be a non-empty object", 400)

    supported_dbs = set(list_supported_databases())

    normalized: Dict[str, Any] = {}
    for table_name, raw_config in policy_json.items():
        _validate_identifier(table_name)
        if not isinstance(raw_config, dict):
            raise SQLGWPolicyError("SQLGW_POLICY_INVALID", f"Table config for '{table_name}' must be object", 400)

        db_alias = raw_config.get("db")
        if db_alias not in supported_dbs:
            raise SQLGWPolicyError("SQLGW_POLICY_INVALID", f"Table '{table_name}' has unsupported db '{db_alias}'", 400)

        table_kind = str(raw_config.get("table_kind", "table")).lower()
        if table_kind not in {"table", "view"}:
            raise SQLGWPolicyError("SQLGW_POLICY_INVALID", f"Table '{table_name}' has invalid table_kind", 400)

        operations = raw_config.get("operations")
        if not isinstance(operations, list) or not operations:
            raise SQLGWPolicyError("SQLGW_POLICY_INVALID", f"Table '{table_name}' must define non-empty operations", 400)

        operations = [str(op) for op in operations]
        if any(op not in ALLOWED_OPERATIONS for op in operations):
            raise SQLGWPolicyError("SQLGW_POLICY_INVALID", f"Table '{table_name}' has invalid operations", 400)

        if table_kind == "view" and any(op in {"insert", "update", "delete"} for op in operations):
            # Explicitly allowed by config if present, but keep deliberate opt-in.
            pass

        normalized_entry = {
            "db": db_alias,
            "table_kind": table_kind,
            "operations": operations,
            "select_columns": [],
            "filter_columns": [],
            "group_columns": [],
            "order_columns": [],
            "insert_columns": [],
            "update_columns": [],
            "max_write_rows": int(raw_config.get("max_write_rows") or get_settings().SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT),
            "allow_explicit_pk_insert": bool(raw_config.get("allow_explicit_pk_insert", False)),
        }

        if "select" in operations:
            normalized_entry["select_columns"] = _normalize_identifier_list(
                raw_config.get("select_columns"), "select_columns", True
            )
            normalized_entry["filter_columns"] = _normalize_identifier_list(
                raw_config.get("filter_columns") or [], "filter_columns", False
            )
            normalized_entry["group_columns"] = _normalize_identifier_list(
                raw_config.get("group_columns") or [], "group_columns", False
            )
            normalized_entry["order_columns"] = _normalize_identifier_list(
                raw_config.get("order_columns") or [], "order_columns", False
            )

        if "insert" in operations:
            normalized_entry["insert_columns"] = _normalize_identifier_list(
                raw_config.get("insert_columns"), "insert_columns", True
            )

        if "update" in operations:
            normalized_entry["update_columns"] = _normalize_identifier_list(
                raw_config.get("update_columns"), "update_columns", True
            )
            normalized_entry["filter_columns"] = _normalize_identifier_list(
                raw_config.get("filter_columns"), "filter_columns", True
            )

        if "delete" in operations:
            normalized_entry["filter_columns"] = _normalize_identifier_list(
                raw_config.get("filter_columns"), "filter_columns", True
            )

        normalized[table_name] = normalized_entry

    if validate_schema:
        _validate_against_schema(normalized)

    return normalized


def create_policy_draft(
    policy_json: Dict[str, Any],
    created_by: str,
    notes: Optional[str] = None,
    validate_schema: bool = True,
) -> Dict[str, Any]:
    _ensure_policy_table()
    engine = _get_policy_engine()

    normalized = validate_policy_json(policy_json, validate_schema=validate_schema)
    checksum = _checksum(normalized)
    now = datetime.utcnow()

    with engine.begin() as conn:
        max_stmt = select(_policy_table.c.version).order_by(desc(_policy_table.c.version)).limit(1)
        current = conn.execute(max_stmt).scalar_one_or_none()
        next_version = int(current or 0) + 1

        payload = {
            "version": next_version,
            "status": "draft",
            "policy_json": _serialize_policy_json(normalized),
            "checksum_sha256": checksum,
            "created_by": str(created_by),
            "created_at": now,
            "updated_by": str(created_by),
            "updated_at": now,
            "notes": notes,
        }
        conn.execute(_policy_table.insert().values(**payload))

        row_stmt = select(_policy_table).where(_policy_table.c.version == next_version).limit(1)
        row = conn.execute(row_stmt).first()

    clear_policy_cache()
    if row is None:
        raise SQLGWPolicyError("SQLGW_EXECUTION_FAILED", "Could not load created policy", 500)
    return _row_to_dict(row, include_policy_json=True)


def _transition_policy(policy_id: int, expected_status: str, new_status: str, user_id: str) -> Dict[str, Any]:
    _ensure_policy_table()
    engine = _get_policy_engine()
    now = datetime.utcnow()

    with engine.begin() as conn:
        row_stmt = select(_policy_table).where(_policy_table.c.id == int(policy_id)).limit(1)
        row = conn.execute(row_stmt).first()
        if row is None:
            raise SQLGWPolicyError("SQLGW_POLICY_NOT_FOUND", f"Policy id {policy_id} not found", 404)

        current_status = str(row._mapping["status"])
        if current_status != expected_status:
            raise SQLGWPolicyError(
                "SQLGW_POLICY_INVALID",
                f"Policy {policy_id} must be '{expected_status}' (current: '{current_status}')",
                400,
            )

        updates: Dict[str, Any] = {
            "status": new_status,
            "updated_by": str(user_id),
            "updated_at": now,
        }
        if new_status == "approved":
            updates["approved_by"] = str(user_id)
            updates["approved_at"] = now
        if new_status == "active":
            conn.execute(
                _policy_table.update()
                .where(and_(_policy_table.c.status == "active", _policy_table.c.id != int(policy_id)))
                .values(
                    status="archived",
                    updated_by=str(user_id),
                    updated_at=now,
                )
            )
            updates["activated_by"] = str(user_id)
            updates["activated_at"] = now

        conn.execute(_policy_table.update().where(_policy_table.c.id == int(policy_id)).values(**updates))

        refreshed = conn.execute(row_stmt).first()

    clear_policy_cache()
    if refreshed is None:
        raise SQLGWPolicyError("SQLGW_EXECUTION_FAILED", "Could not load policy after transition", 500)
    return _row_to_dict(refreshed, include_policy_json=True)


def approve_policy(policy_id: int, approved_by: str) -> Dict[str, Any]:
    return _transition_policy(policy_id, expected_status="draft", new_status="approved", user_id=approved_by)


def activate_policy(policy_id: int, activated_by: str) -> Dict[str, Any]:
    return _transition_policy(policy_id, expected_status="approved", new_status="active", user_id=activated_by)


def archive_policy(policy_id: int, archived_by: str) -> Dict[str, Any]:
    _ensure_policy_table()
    engine = _get_policy_engine()

    with engine.begin() as conn:
        row_stmt = select(_policy_table).where(_policy_table.c.id == int(policy_id)).limit(1)
        row = conn.execute(row_stmt).first()
        if row is None:
            raise SQLGWPolicyError("SQLGW_POLICY_NOT_FOUND", f"Policy id {policy_id} not found", 404)

        status = str(row._mapping["status"])
        if status == "active":
            raise SQLGWPolicyError("SQLGW_POLICY_INVALID", "Active policy cannot be archived directly", 400)

        conn.execute(
            _policy_table.update()
            .where(_policy_table.c.id == int(policy_id))
            .values(
                status="archived",
                updated_by=str(archived_by),
                updated_at=datetime.utcnow(),
            )
        )
        refreshed = conn.execute(row_stmt).first()

    clear_policy_cache()
    if refreshed is None:
        raise SQLGWPolicyError("SQLGW_EXECUTION_FAILED", "Could not load policy after archive", 500)
    return _row_to_dict(refreshed, include_policy_json=True)


def get_active_policy() -> Optional[Dict[str, Any]]:
    _ensure_policy_table()
    engine = _get_policy_engine()

    with engine.connect() as conn:
        stmt = (
            select(_policy_table)
            .where(_policy_table.c.status == "active")
            .order_by(desc(_policy_table.c.version))
            .limit(1)
        )
        row = conn.execute(stmt).first()

    if row is None:
        return None
    return _row_to_dict(row, include_policy_json=True)


def load_active_policy_cached() -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    settings = get_settings()
    ttl_seconds = int(getattr(settings, "SQL_GATEWAY_POLICY_CACHE_TTL_SECONDS", 60))

    now = time.time()
    with _policy_cache_lock:
        cached_version = _policy_cache.get("version")
        cached_policy = _policy_cache.get("policy")
        cached_loaded_at = float(_policy_cache.get("loaded_at") or 0)

        if cached_policy and (now - cached_loaded_at) < ttl_seconds:
            return cached_version, cached_policy

    try:
        active = get_active_policy()
    except Exception as exc:
        with _policy_cache_lock:
            cached_policy = _policy_cache.get("policy")
            cached_version = _policy_cache.get("version")
        if cached_policy:
            return cached_version, cached_policy
        if isinstance(exc, SQLGWPolicyError):
            raise
        raise SQLGWPolicyError("SQLGW_CONFIG_INVALID", f"Could not load active SQLGW policy: {exc}", 503) from exc

    if not active:
        with _policy_cache_lock:
            _policy_cache["version"] = None
            _policy_cache["policy"] = None
            _policy_cache["loaded_at"] = now
        return None, None

    policy_json = active.get("policy_json") or {}
    if not isinstance(policy_json, dict) or not policy_json:
        raise SQLGWPolicyError("SQLGW_CONFIG_INVALID", "Active SQLGW policy is empty or invalid", 503)

    version = int(active["version"])
    with _policy_cache_lock:
        _policy_cache["version"] = version
        _policy_cache["policy"] = policy_json
        _policy_cache["loaded_at"] = now

    return version, policy_json

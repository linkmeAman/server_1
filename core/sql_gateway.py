"""SQL gateway service for structured, single-table operations."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Tuple, Union, Annotated

from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import MetaData, Table, and_, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.sql import Select
from sqlalchemy.sql import sqltypes

from core.database import engines
from core.settings import get_settings
from core.sqlgw_policy_store import SQLGWPolicyError, load_active_policy_cached

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SQLGatewayError(Exception):
    """Domain error for gateway validation and execution."""

    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class FilterSpec(BaseModel):
    column: str
    op: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "like",
        "ilike",
        "between",
        "is_null",
        "not_null",
    ]
    value: Any = None


class OrderBySpec(BaseModel):
    column: str
    direction: Literal["asc", "desc"] = "asc"


class AggregateSpec(BaseModel):
    func: Literal["count", "sum", "avg", "min", "max"]
    column: str
    alias: Optional[str] = None


class SelectRequest(BaseModel):
    operation: Literal["select"]
    table: str
    columns: List[str]
    filters: Optional[List[FilterSpec]] = None
    group_by: Optional[List[str]] = None
    aggregates: Optional[List[AggregateSpec]] = None
    order_by: Optional[List[OrderBySpec]] = None
    limit: Optional[int] = None
    offset: int = 0
    include_total: bool = False


class InsertRequest(BaseModel):
    operation: Literal["insert"]
    table: str
    rows: Union[Dict[str, Any], List[Dict[str, Any]]]


class UpdateRequest(BaseModel):
    operation: Literal["update"]
    table: str
    values: Dict[str, Any]
    filters: List[FilterSpec]


class DeleteRequest(BaseModel):
    operation: Literal["delete"]
    table: str
    filters: List[FilterSpec]


GatewayRequest = Annotated[
    Union[SelectRequest, InsertRequest, UpdateRequest, DeleteRequest],
    Field(discriminator="operation"),
]

_gateway_request_adapter = TypeAdapter(GatewayRequest)


@dataclass
class ResolvedTable:
    engine_key: str
    engine: Engine
    table_obj: Table
    columns_map: Dict[str, Any]
    config: Dict[str, Any]


_metadata_lock = threading.Lock()
_metadata_cache: Dict[Tuple[str, str], Tuple[Table, Dict[str, Any]]] = {}


def clear_metadata_cache() -> None:
    with _metadata_lock:
        _metadata_cache.clear()


def metadata_cache_size() -> int:
    with _metadata_lock:
        return len(_metadata_cache)


def parse_gateway_payload(payload: Dict[str, Any]) -> GatewayRequest:
    try:
        return _gateway_request_adapter.validate_python(payload)
    except ValidationError as exc:
        raise SQLGatewayError(
            code="SQLGW_INVALID_OPERATOR_PAYLOAD",
            message=f"Invalid request payload: {exc.errors()}",
            status_code=400,
        ) from exc


def execute_gateway_request(request_model: GatewayRequest, actor_user_id: Optional[str] = None) -> Dict[str, Any]:
    settings = get_settings()
    allowlist = _load_allowlist(settings)
    db_engine_map = _load_db_engine_map(settings)

    _validate_identifier(request_model.table)
    resolved = _resolve_table(request_model.table, request_model.operation, allowlist, db_engine_map)

    if isinstance(request_model, SelectRequest):
        return _run_select(request_model, resolved, settings)
    if isinstance(request_model, InsertRequest):
        return _run_insert(request_model, resolved, settings, actor_user_id=actor_user_id)
    if isinstance(request_model, UpdateRequest):
        return _run_update(request_model, resolved, settings, actor_user_id=actor_user_id)
    if isinstance(request_model, DeleteRequest):
        return _run_delete(request_model, resolved, settings, actor_user_id=actor_user_id)

    raise SQLGatewayError("SQLGW_EXECUTION_FAILED", "Unsupported operation", 500)


def _load_allowlist(settings) -> Dict[str, Any]:
    source = str(getattr(settings, "SQL_GATEWAY_ALLOWLIST_SOURCE", "auto")).strip().lower()
    if source not in {"auto", "env", "file", "db"}:
        source = "auto"

    if source == "env":
        allowlist = _load_allowlist_from_env(settings)
    elif source == "file":
        allowlist = _load_allowlist_from_file(settings)
    elif source == "db":
        allowlist = _load_allowlist_from_db(settings)
    else:
        allowlist = (
            _load_allowlist_from_env(settings, required=False)
            or _load_allowlist_from_file(settings, required=False)
            or _load_allowlist_from_db(settings, required=True)
        )

    if not isinstance(allowlist, dict) or not allowlist:
        raise SQLGatewayError(
            "SQLGW_CONFIG_INVALID",
            "SQL gateway allowlist is missing or invalid",
            503,
        )
    return allowlist


def _load_allowlist_from_env(settings, required: bool = True) -> Dict[str, Any]:
    allowlist = getattr(settings, "SQL_GATEWAY_ALLOWLIST", {})
    if not allowlist and not required:
        return {}
    if not isinstance(allowlist, dict):
        if required:
            raise SQLGatewayError("SQLGW_CONFIG_INVALID", "Allowlist env config must be a JSON object", 503)
        return {}
    if allowlist.get("__invalid__") is True:
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", "Allowlist env config is invalid JSON", 503)
    return allowlist


def _load_allowlist_from_file(settings, required: bool = True) -> Dict[str, Any]:
    allowlist_path = str(getattr(settings, "SQL_GATEWAY_ALLOWLIST_PATH", "") or "").strip()
    if not allowlist_path:
        if required:
            raise SQLGatewayError("SQLGW_CONFIG_INVALID", "Allowlist file path is not configured", 503)
        return {}

    if not os.path.exists(allowlist_path):
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", "Allowlist file does not exist", 503)

    try:
        with open(allowlist_path, "r", encoding="utf-8") as file:
            parsed = json.load(file)
    except Exception as exc:
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", f"Allowlist file is invalid JSON: {exc}", 503) from exc

    if not isinstance(parsed, dict):
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", "Allowlist file must contain a JSON object", 503)
    return parsed


def _load_allowlist_from_db(settings, required: bool = True) -> Dict[str, Any]:
    try:
        _, policy_json = load_active_policy_cached()
    except SQLGWPolicyError as exc:
        raise SQLGatewayError(exc.code, exc.message, exc.status_code) from exc
    except Exception as exc:
        raise SQLGatewayError(
            "SQLGW_CONFIG_INVALID",
            f"Failed to load active SQL gateway policy: {exc}",
            503,
        ) from exc

    if not policy_json:
        if required:
            raise SQLGatewayError("SQLGW_CONFIG_INVALID", "No active SQL gateway policy found", 503)
        return {}

    if not isinstance(policy_json, dict):
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", "Active SQL gateway policy is invalid", 503)
    return policy_json


def _load_db_engine_map(settings) -> Dict[str, str]:
    db_map = getattr(settings, "SQL_GATEWAY_DB_ENGINE_MAP", {})
    if not isinstance(db_map, dict) or db_map.get("__invalid__") is True:
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", "DB engine map config is invalid", 503)
    if not db_map:
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", "DB engine map config is missing", 503)
    return db_map


def _resolve_table(
    table_name: str,
    operation: str,
    allowlist: Dict[str, Any],
    db_engine_map: Dict[str, str],
) -> ResolvedTable:
    if table_name not in allowlist:
        raise SQLGatewayError("SQLGW_FORBIDDEN_TABLE", f"Table '{table_name}' is not allowed", 403)

    config = allowlist[table_name]
    if not isinstance(config, dict):
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", f"Invalid allowlist entry for '{table_name}'", 503)

    allowed_ops = config.get("operations") or []
    if operation not in allowed_ops:
        raise SQLGatewayError("SQLGW_FORBIDDEN_TABLE", f"Operation '{operation}' is not allowed", 403)

    db_alias = config.get("db")
    if not isinstance(db_alias, str) or not db_alias:
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", f"Missing db alias for '{table_name}'", 503)

    engine_key = db_engine_map.get(db_alias)
    if not isinstance(engine_key, str) or not engine_key:
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", f"DB mapping missing for alias '{db_alias}'", 503)

    engine = engines.get(engine_key)
    if engine is None:
        raise SQLGatewayError("SQLGW_CONFIG_INVALID", f"Engine '{engine_key}' is not available", 503)

    table_obj, columns_map = _get_table_metadata(engine_key, engine, table_name)
    return ResolvedTable(
        engine_key=engine_key,
        engine=engine,
        table_obj=table_obj,
        columns_map=columns_map,
        config=config,
    )


def _get_table_metadata(engine_key: str, engine: Engine, table_name: str) -> Tuple[Table, Dict[str, Any]]:
    cache_key = (engine_key, table_name)
    with _metadata_lock:
        cached = _metadata_cache.get(cache_key)
        if cached is not None:
            return cached

    try:
        metadata = MetaData()
        table_obj = Table(table_name, metadata, autoload_with=engine)
    except Exception as exc:
        raise SQLGatewayError(
            "SQLGW_EXECUTION_FAILED",
            f"Could not reflect table '{table_name}'",
            500,
        ) from exc

    columns_map = {col.name: col for col in table_obj.columns}

    with _metadata_lock:
        existing = _metadata_cache.get(cache_key)
        if existing is not None:
            return existing
        _metadata_cache[cache_key] = (table_obj, columns_map)
    return table_obj, columns_map


def _validate_identifier(name: str) -> None:
    if not isinstance(name, str) or not IDENTIFIER_PATTERN.match(name) or name == "*":
        raise SQLGatewayError("SQLGW_INVALID_IDENTIFIER", f"Invalid identifier '{name}'", 400)


def _validate_identifier_list(names: List[str]) -> None:
    for name in names:
        _validate_identifier(name)


def _get_limit_value(request_limit: Optional[int], settings) -> int:
    default_limit = int(settings.SQL_GATEWAY_DEFAULT_LIMIT)
    max_limit = int(settings.SQL_GATEWAY_MAX_LIMIT)
    if request_limit is None:
        return default_limit
    if request_limit <= 0 or request_limit > max_limit:
        raise SQLGatewayError(
            "SQLGW_COMPLEXITY_LIMIT_EXCEEDED",
            f"limit must be between 1 and {max_limit}",
            400,
        )
    return request_limit


def _enforce_max_size(value: int, cap: int, label: str) -> None:
    if value > cap:
        raise SQLGatewayError(
            "SQLGW_COMPLEXITY_LIMIT_EXCEEDED",
            f"{label} exceeds limit of {cap}",
            400,
        )


def _enforce_allowed_columns(columns: List[str], allowed_columns: List[str]) -> None:
    allowed_set = set(allowed_columns)
    for col in columns:
        if col not in allowed_set:
            raise SQLGatewayError("SQLGW_FORBIDDEN_COLUMN", f"Column '{col}' is not allowed", 403)


def _normalize_actor_user_id(actor_user_id: Optional[str]) -> Optional[str]:
    if actor_user_id is None:
        return None
    normalized = str(actor_user_id).strip()
    return normalized or None


def _stamp_actor_column(
    payload: Dict[str, Any],
    actor_user_id: Optional[str],
    allowed_columns: List[str],
    column_map: Dict[str, Any],
    column_name: str,
) -> None:
    if actor_user_id and column_name in column_map and column_name in allowed_columns:
        payload[column_name] = actor_user_id


def _run_select(request: SelectRequest, resolved: ResolvedTable, settings) -> Dict[str, Any]:
    columns = request.columns or []
    if not columns:
        raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "columns must be non-empty", 400)

    _validate_identifier_list(columns)

    filters = request.filters or []
    group_by = request.group_by or []
    aggregates = request.aggregates or []
    order_by = request.order_by or []

    _enforce_max_size(len(columns), int(settings.SQL_GATEWAY_MAX_COLUMNS), "columns")
    _enforce_max_size(len(filters), int(settings.SQL_GATEWAY_MAX_FILTERS), "filters")
    _enforce_max_size(len(group_by), int(settings.SQL_GATEWAY_MAX_GROUP_BY), "group_by")
    _enforce_max_size(len(order_by), int(settings.SQL_GATEWAY_MAX_ORDER_BY), "order_by")

    if request.offset < 0:
        raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "offset must be >= 0", 400)

    if request.offset > 0 and not order_by:
        raise SQLGatewayError(
            "SQLGW_DETERMINISTIC_ORDER_REQUIRED",
            "order_by is required when offset > 0",
            400,
        )

    select_allowed = list(resolved.config.get("select_columns", []))
    filter_allowed = list(resolved.config.get("filter_columns", []))
    group_allowed = list(resolved.config.get("group_columns", []))
    order_allowed = list(resolved.config.get("order_columns", []))

    _enforce_allowed_columns(columns, select_allowed)

    for col in group_by:
        _validate_identifier(col)
    _enforce_allowed_columns(group_by, group_allowed)

    for order in order_by:
        _validate_identifier(order.column)
    _enforce_allowed_columns([order.column for order in order_by], order_allowed)

    select_exprs = [resolved.table_obj.c[col] for col in columns]
    for aggregate in aggregates:
        _validate_identifier(aggregate.column)
        if aggregate.column not in select_allowed:
            raise SQLGatewayError("SQLGW_FORBIDDEN_COLUMN", f"Column '{aggregate.column}' is not allowed", 403)
        source_col = resolved.table_obj.c[aggregate.column]
        agg_expr = _build_aggregate_expression(aggregate.func, source_col)
        if aggregate.alias:
            _validate_identifier(aggregate.alias)
            agg_expr = agg_expr.label(aggregate.alias)
        select_exprs.append(agg_expr)

    where_clauses = _build_filters(
        filters=filters,
        column_map=resolved.columns_map,
        allowed_filter_columns=filter_allowed,
        max_in_list=int(settings.SQL_GATEWAY_MAX_IN_LIST),
        dialect_name=resolved.engine.dialect.name,
    )

    stmt: Select = select(*select_exprs).select_from(resolved.table_obj)
    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))
    if group_by:
        stmt = stmt.group_by(*[resolved.table_obj.c[col] for col in group_by])

    for order in order_by:
        order_col = resolved.table_obj.c[order.column]
        stmt = stmt.order_by(order_col.asc() if order.direction == "asc" else order_col.desc())

    limit_value = _get_limit_value(request.limit, settings)
    stmt = stmt.limit(limit_value).offset(request.offset)
    stmt = _apply_statement_timeout_hint(stmt, resolved.engine, int(settings.SQL_GATEWAY_STATEMENT_TIMEOUT_MS))

    try:
        with resolved.engine.connect() as connection:
            rows = connection.execute(stmt).mappings().all()
            data_rows = [_serialize_row(dict(row)) for row in rows]

            response: Dict[str, Any] = {
                "rows": data_rows,
                "returned_count": len(data_rows),
                "limit": limit_value,
                "offset": request.offset,
            }

            if request.include_total and bool(settings.SQL_GATEWAY_ENABLE_TOTAL_COUNT):
                count_stmt = _build_total_count_statement(resolved.table_obj, where_clauses, group_by)
                count_stmt = _apply_statement_timeout_hint(
                    count_stmt,
                    resolved.engine,
                    int(settings.SQL_GATEWAY_STATEMENT_TIMEOUT_MS),
                )
                total_count = connection.execute(count_stmt).scalar_one()
                response["total_count"] = int(total_count)

            return response
    except OperationalError as exc:
        _raise_timeout_or_execution_error(exc)
    except SQLAlchemyError as exc:
        raise SQLGatewayError("SQLGW_EXECUTION_FAILED", f"Query execution failed: {exc}", 500) from exc


def _run_insert(
    request: InsertRequest,
    resolved: ResolvedTable,
    settings,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    insert_allowed = list(resolved.config.get("insert_columns", []))
    _rows = request.rows if isinstance(request.rows, list) else [request.rows]
    actor_value = _normalize_actor_user_id(actor_user_id)

    _enforce_max_size(len(_rows), int(settings.SQL_GATEWAY_MAX_BULK_INSERT_ROWS), "rows")
    if not _rows:
        raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "rows must be non-empty", 400)

    table_pk_columns = {col.name for col in resolved.table_obj.primary_key.columns}
    allow_explicit_pk_insert = bool(resolved.config.get("allow_explicit_pk_insert", False))

    prepared_rows: List[Dict[str, Any]] = []
    for row in _rows:
        if not isinstance(row, dict):
            raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "Each row must be an object", 400)
        if not row:
            raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "Each row must contain at least one column", 400)

        prepared_row = dict(row)
        if "park" in resolved.columns_map and "park" in insert_allowed and "park" not in prepared_row:
            prepared_row["park"] = resolved.config.get("soft_delete_active_value", 0)
        _stamp_actor_column(prepared_row, actor_value, insert_allowed, resolved.columns_map, "created_by")
        _stamp_actor_column(prepared_row, actor_value, insert_allowed, resolved.columns_map, "updated_by")

        keys = list(prepared_row.keys())
        _validate_identifier_list(keys)
        _enforce_allowed_columns(keys, insert_allowed)

        if not allow_explicit_pk_insert and any(col in prepared_row for col in table_pk_columns):
            raise SQLGatewayError("SQLGW_FORBIDDEN_COLUMN", "Explicit primary key insert is not allowed", 403)

        if len(keys) < 1:
            raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "Row has no allowed columns", 400)

        prepared_rows.append(prepared_row)

    stmt = insert(resolved.table_obj)

    try:
        with resolved.engine.begin() as connection:
            if len(prepared_rows) == 1:
                result = connection.execute(stmt.values(prepared_rows[0]))
                inserted_pk = [v for v in (result.inserted_primary_key or []) if v is not None]
            else:
                result = connection.execute(stmt, prepared_rows)
                inserted_pk = []

            affected_rows = int(result.rowcount or 0)
            return {
                "affected_rows": affected_rows,
                "inserted_primary_keys": inserted_pk,
            }
    except OperationalError as exc:
        _raise_timeout_or_execution_error(exc)
    except SQLAlchemyError as exc:
        raise SQLGatewayError("SQLGW_EXECUTION_FAILED", f"Insert failed: {exc}", 500) from exc


def _run_update(
    request: UpdateRequest,
    resolved: ResolvedTable,
    settings,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(request.values, dict) or not request.values:
        raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "values must be a non-empty object", 400)
    if not request.filters:
        raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "filters must be non-empty", 400)

    _enforce_max_size(len(request.filters), int(settings.SQL_GATEWAY_MAX_FILTERS), "filters")

    update_allowed = list(resolved.config.get("update_columns", []))
    filter_allowed = list(resolved.config.get("filter_columns", []))

    values_payload = dict(request.values)
    actor_value = _normalize_actor_user_id(actor_user_id)
    _stamp_actor_column(values_payload, actor_value, update_allowed, resolved.columns_map, "updated_by")

    value_keys = list(values_payload.keys())
    _validate_identifier_list(value_keys)
    _enforce_allowed_columns(value_keys, update_allowed)

    where_clauses = _build_filters(
        filters=request.filters,
        column_map=resolved.columns_map,
        allowed_filter_columns=filter_allowed,
        max_in_list=int(settings.SQL_GATEWAY_MAX_IN_LIST),
        dialect_name=resolved.engine.dialect.name,
    )

    if not where_clauses:
        raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "filters must be non-empty", 400)

    write_cap = int(resolved.config.get("max_write_rows") or settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT)

    try:
        with resolved.engine.begin() as connection:
            matched_rows = _count_matched_rows(connection, resolved.table_obj, where_clauses)
            if matched_rows > write_cap:
                raise SQLGatewayError(
                    "SQLGW_WRITE_LIMIT_EXCEEDED",
                    f"Matched rows {matched_rows} exceed cap {write_cap}",
                    400,
                )

            stmt = update(resolved.table_obj).where(and_(*where_clauses)).values(**values_payload)
            result = connection.execute(stmt)
            affected_rows = int(result.rowcount or 0)
            if affected_rows > write_cap:
                raise SQLGatewayError(
                    "SQLGW_WRITE_LIMIT_EXCEEDED",
                    f"Affected rows {affected_rows} exceed cap {write_cap}",
                    400,
                )

            return {"affected_rows": affected_rows, "inserted_primary_keys": []}
    except OperationalError as exc:
        _raise_timeout_or_execution_error(exc)
    except SQLGatewayError:
        raise
    except SQLAlchemyError as exc:
        raise SQLGatewayError("SQLGW_EXECUTION_FAILED", f"Update failed: {exc}", 500) from exc


def _run_delete(
    request: DeleteRequest,
    resolved: ResolvedTable,
    settings,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not request.filters:
        raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "filters must be non-empty", 400)

    _enforce_max_size(len(request.filters), int(settings.SQL_GATEWAY_MAX_FILTERS), "filters")

    filter_allowed = list(resolved.config.get("filter_columns", []))
    update_allowed = list(resolved.config.get("update_columns", []))

    soft_delete_column = str(resolved.config.get("soft_delete_column") or "park")
    _validate_identifier(soft_delete_column)
    if soft_delete_column not in resolved.columns_map:
        raise SQLGatewayError(
            "SQLGW_FORBIDDEN_COLUMN",
            f"Soft-delete column '{soft_delete_column}' does not exist",
            403,
        )
    if soft_delete_column not in update_allowed:
        raise SQLGatewayError(
            "SQLGW_FORBIDDEN_COLUMN",
            f"Soft-delete column '{soft_delete_column}' must be allowed in update_columns",
            403,
        )

    soft_delete_value = resolved.config.get("soft_delete_deleted_value", 1)
    soft_delete_payload: Dict[str, Any] = {soft_delete_column: soft_delete_value}

    actor_value = _normalize_actor_user_id(actor_user_id)
    if actor_value:
        if "parked_by" in resolved.columns_map and "parked_by" in update_allowed:
            soft_delete_payload["parked_by"] = actor_value
        elif "updated_by" in resolved.columns_map and "updated_by" in update_allowed:
            soft_delete_payload["updated_by"] = actor_value

    where_clauses = _build_filters(
        filters=request.filters,
        column_map=resolved.columns_map,
        allowed_filter_columns=filter_allowed,
        max_in_list=int(settings.SQL_GATEWAY_MAX_IN_LIST),
        dialect_name=resolved.engine.dialect.name,
    )

    if not where_clauses:
        raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", "filters must be non-empty", 400)

    write_cap = int(resolved.config.get("max_write_rows") or settings.SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT)

    try:
        with resolved.engine.begin() as connection:
            matched_rows = _count_matched_rows(connection, resolved.table_obj, where_clauses)
            if matched_rows > write_cap:
                raise SQLGatewayError(
                    "SQLGW_WRITE_LIMIT_EXCEEDED",
                    f"Matched rows {matched_rows} exceed cap {write_cap}",
                    400,
                )

            stmt = update(resolved.table_obj).where(and_(*where_clauses)).values(**soft_delete_payload)
            result = connection.execute(stmt)
            affected_rows = int(result.rowcount or 0)
            if affected_rows > write_cap:
                raise SQLGatewayError(
                    "SQLGW_WRITE_LIMIT_EXCEEDED",
                    f"Affected rows {affected_rows} exceed cap {write_cap}",
                    400,
                )

            return {"affected_rows": affected_rows, "inserted_primary_keys": []}
    except OperationalError as exc:
        _raise_timeout_or_execution_error(exc)
    except SQLGatewayError:
        raise
    except SQLAlchemyError as exc:
        raise SQLGatewayError("SQLGW_EXECUTION_FAILED", f"Soft delete failed: {exc}", 500) from exc


def _build_total_count_statement(table_obj: Table, where_clauses: List[Any], group_by: List[str]) -> Select:
    if group_by:
        grouped = select(*[table_obj.c[col] for col in group_by]).select_from(table_obj)
        if where_clauses:
            grouped = grouped.where(and_(*where_clauses))
        grouped = grouped.group_by(*[table_obj.c[col] for col in group_by]).subquery()
        return select(func.count()).select_from(grouped)

    count_stmt = select(func.count()).select_from(table_obj)
    if where_clauses:
        count_stmt = count_stmt.where(and_(*where_clauses))
    return count_stmt


def _build_aggregate_expression(func_name: str, source_col: Any) -> Any:
    if func_name == "count":
        return func.count(source_col)
    if func_name == "sum":
        return func.sum(source_col)
    if func_name == "avg":
        return func.avg(source_col)
    if func_name == "min":
        return func.min(source_col)
    if func_name == "max":
        return func.max(source_col)
    raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", f"Unsupported aggregate '{func_name}'", 400)


def _build_filters(
    filters: List[FilterSpec],
    column_map: Dict[str, Any],
    allowed_filter_columns: List[str],
    max_in_list: int,
    dialect_name: str,
) -> List[Any]:
    clauses = []
    for flt in filters:
        _validate_identifier(flt.column)
    _enforce_allowed_columns([flt.column for flt in filters], allowed_filter_columns)

    for flt in filters:
        if flt.column not in column_map:
            raise SQLGatewayError("SQLGW_FORBIDDEN_COLUMN", f"Column '{flt.column}' does not exist", 403)
        column = column_map[flt.column]
        clauses.append(_build_single_filter_clause(column, flt, max_in_list, dialect_name))

    return clauses


def _build_single_filter_clause(column: Any, flt: FilterSpec, max_in_list: int, dialect_name: str) -> Any:
    op = flt.op
    value = flt.value

    if op in {"is_null", "not_null"}:
        if value is not None:
            raise SQLGatewayError(
                "SQLGW_INVALID_OPERATOR_PAYLOAD",
                f"Operator '{op}' does not accept value",
                400,
            )
        return column.is_(None) if op == "is_null" else column.is_not(None)

    if op in {"in", "not_in"}:
        if not isinstance(value, list) or not value:
            raise SQLGatewayError(
                "SQLGW_INVALID_OPERATOR_PAYLOAD",
                f"Operator '{op}' requires a non-empty list",
                400,
            )
        _enforce_max_size(len(value), max_in_list, "in-list")
        coerced = [_coerce_value(column, item, op) for item in value]
        return column.in_(coerced) if op == "in" else ~column.in_(coerced)

    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise SQLGatewayError(
                "SQLGW_INVALID_OPERATOR_PAYLOAD",
                "Operator 'between' requires exactly 2 values",
                400,
            )
        left = _coerce_value(column, value[0], op)
        right = _coerce_value(column, value[1], op)
        return column.between(left, right)

    if op in {"like", "ilike"}:
        if not isinstance(value, str):
            raise SQLGatewayError(
                "SQLGW_INVALID_OPERATOR_PAYLOAD",
                f"Operator '{op}' requires a string value",
                400,
            )
        if op == "like":
            return column.like(value)
        if dialect_name == "postgresql":
            return column.ilike(value)
        return func.lower(column).like(value.lower())

    coerced_value = _coerce_value(column, value, op)

    if op == "eq":
        return column == coerced_value
    if op == "ne":
        return column != coerced_value
    if op == "gt":
        return column > coerced_value
    if op == "gte":
        return column >= coerced_value
    if op == "lt":
        return column < coerced_value
    if op == "lte":
        return column <= coerced_value

    raise SQLGatewayError("SQLGW_INVALID_OPERATOR_PAYLOAD", f"Unsupported operator '{op}'", 400)


def _coerce_value(column: Any, value: Any, op: str) -> Any:
    if value is None:
        if op in {"eq", "ne"}:
            return value
        raise SQLGatewayError(
            "SQLGW_INVALID_OPERATOR_PAYLOAD",
            f"Operator '{op}' requires a value",
            400,
        )

    col_type = column.type

    if isinstance(col_type, (sqltypes.Integer, sqltypes.BigInteger, sqltypes.SmallInteger)):
        try:
            if isinstance(value, bool):
                raise ValueError("bool not allowed")
            if isinstance(value, str) and value.strip() == "":
                raise ValueError("empty")
            return int(value)
        except Exception as exc:
            raise SQLGatewayError(
                "SQLGW_INVALID_OPERATOR_PAYLOAD",
                f"Column '{column.name}' expects integer-compatible value",
                400,
            ) from exc

    if isinstance(col_type, (sqltypes.Numeric, sqltypes.Float, sqltypes.DECIMAL, sqltypes.REAL)):
        try:
            if isinstance(value, bool):
                raise ValueError("bool not allowed")
            return float(Decimal(str(value)))
        except Exception as exc:
            raise SQLGatewayError(
                "SQLGW_INVALID_OPERATOR_PAYLOAD",
                f"Column '{column.name}' expects numeric-compatible value",
                400,
            ) from exc

    if isinstance(col_type, sqltypes.DateTime):
        return _parse_datetime_value(column.name, value)

    if isinstance(col_type, sqltypes.Date):
        return _parse_date_value(column.name, value)

    return value


def _parse_datetime_value(column_name: str, value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except Exception as exc:
            raise SQLGatewayError(
                "SQLGW_INVALID_OPERATOR_PAYLOAD",
                f"Column '{column_name}' expects ISO datetime string",
                400,
            ) from exc
    raise SQLGatewayError(
        "SQLGW_INVALID_OPERATOR_PAYLOAD",
        f"Column '{column_name}' expects datetime value",
        400,
    )


def _parse_date_value(column_name: str, value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except Exception as exc:
            raise SQLGatewayError(
                "SQLGW_INVALID_OPERATOR_PAYLOAD",
                f"Column '{column_name}' expects ISO date string",
                400,
            ) from exc
    raise SQLGatewayError(
        "SQLGW_INVALID_OPERATOR_PAYLOAD",
        f"Column '{column_name}' expects date value",
        400,
    )


def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    serialized = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            serialized[key] = value.isoformat()
        elif isinstance(value, Decimal):
            serialized[key] = str(value)
        else:
            serialized[key] = value
    return serialized


def _count_matched_rows(connection, table_obj: Table, where_clauses: List[Any]) -> int:
    stmt = select(func.count()).select_from(table_obj).where(and_(*where_clauses))
    return int(connection.execute(stmt).scalar_one())


def _apply_statement_timeout_hint(stmt: Select, engine: Engine, timeout_ms: int) -> Select:
    if timeout_ms <= 0:
        return stmt

    # Best effort for MySQL path; no universal SQLAlchemy timeout semantics.
    if engine.dialect.name in {"mysql", "mariadb"}:
        return stmt.prefix_with(f"/*+ MAX_EXECUTION_TIME({timeout_ms}) */")
    return stmt


def _raise_timeout_or_execution_error(exc: OperationalError) -> None:
    text = str(exc).lower()
    if "timeout" in text or "max_execution_time" in text:
        raise SQLGatewayError("SQLGW_EXECUTION_TIMEOUT", "Query execution timed out", 504) from exc
    raise SQLGatewayError("SQLGW_EXECUTION_FAILED", f"Query execution failed: {exc}", 500) from exc

"""Internal SQL Gateway admin and schema endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.response import error_response, success_response
from core.security import validate_token
from core.settings import get_settings
from core.sqlgw_policy_store import (
    SQLGWPolicyError,
    activate_policy,
    approve_policy,
    archive_policy,
    create_policy_draft,
    get_policy_version,
    list_policy_versions,
)
from core.sqlgw_schema import SQLGWSchemaError, list_columns, list_supported_databases, list_tables

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/sqlgw", tags=["sqlgw-admin"])


class PolicyCreateRequest(BaseModel):
    policy_json: Dict[str, Any]
    notes: Optional[str] = None
    validate_schema: bool = True


class SQLGWAdminError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or str(uuid4())


def _error_response(code: str, message: str, status_code: int, request_id: str) -> JSONResponse:
    payload = error_response(
        error=code,
        message=message,
        data={"request_id": request_id},
    ).model_dump(mode="json")
    response = JSONResponse(content=payload, status_code=status_code)
    response.headers["X-Request-ID"] = request_id
    return response


def _success_response(data: Dict[str, Any], request_id: str) -> JSONResponse:
    payload = success_response(
        data={**data, "request_id": request_id},
        message="Success",
    ).model_dump(mode="json")
    response = JSONResponse(content=payload, status_code=200)
    response.headers["X-Request-ID"] = request_id
    return response


def _extract_list_claim(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x).lower() for x in value]
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    return []


def _authenticate_claims(request: Request) -> Dict[str, Any]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise SQLGWAdminError("SQLGW_UNAUTHORIZED", "Missing or invalid Authorization header", 401)

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise SQLGWAdminError("SQLGW_UNAUTHORIZED", "Missing access token", 401)

    try:
        return validate_token(token, expected_type="access")
    except Exception as exc:
        raise SQLGWAdminError("SQLGW_UNAUTHORIZED", "Invalid or expired access token", 401) from exc


def _is_admin_claims(claims: Dict[str, Any]) -> bool:
    if bool(claims.get("is_admin")):
        return True

    roles = set(_extract_list_claim(claims.get("roles")))
    permissions = set(_extract_list_claim(claims.get("permissions")))

    return (
        "sqlgw_admin" in roles
        or "sqlgw_admin" in permissions
        or "sqlgw_admin" in _extract_list_claim(claims.get("role"))
        or "sqlgw_admin" in _extract_list_claim(claims.get("permission"))
        or "sqlgw_admin" in _extract_list_claim(claims.get("scope"))
    )


def _is_approver_claims(claims: Dict[str, Any]) -> bool:
    if _is_admin_claims(claims):
        return True

    roles = set(_extract_list_claim(claims.get("roles")))
    permissions = set(_extract_list_claim(claims.get("permissions")))

    if "sqlgw_approver" in roles:
        return True
    if "sqlgw_approver" in permissions:
        return True

    # Also accept uppercase permission names
    permissions_raw = {str(x) for x in (claims.get("permissions") or [])} if isinstance(claims.get("permissions"), list) else set()
    if "SQLGW_APPROVER" in permissions_raw:
        return True

    return False


def _require_admin(claims: Dict[str, Any]) -> None:
    if not bool(get_settings().SQLGW_ADMIN_REQUIRE_RBAC):
        return
    if not _is_admin_claims(claims):
        raise SQLGWAdminError("SQLGW_FORBIDDEN", "Admin permission required", 403)


def _require_approver(claims: Dict[str, Any]) -> None:
    if not bool(get_settings().SQLGW_ADMIN_REQUIRE_RBAC):
        return
    if not _is_approver_claims(claims):
        raise SQLGWAdminError("SQLGW_FORBIDDEN", "Approver permission required", 403)


def _subject(claims: Dict[str, Any]) -> str:
    return str(claims.get("sub", "unknown"))


@router.get("/schema/databases")
async def get_schema_databases(request: Request):
    request_id = _request_id(request)
    try:
        claims = _authenticate_claims(request)
        _require_admin(claims)
        data = {"databases": list_supported_databases()}
        return _success_response(data, request_id)
    except (SQLGWAdminError, SQLGWSchemaError) as exc:
        return _error_response(exc.code, exc.message, exc.status_code, request_id)


@router.get("/schema/tables")
async def get_schema_tables(request: Request, db: str = Query(...)):
    request_id = _request_id(request)
    try:
        claims = _authenticate_claims(request)
        _require_admin(claims)
        data = {"db": db, "tables": list_tables(db)}
        return _success_response(data, request_id)
    except (SQLGWAdminError, SQLGWSchemaError) as exc:
        return _error_response(exc.code, exc.message, exc.status_code, request_id)


@router.get("/schema/columns")
async def get_schema_columns(request: Request, db: str = Query(...), table: str = Query(...)):
    request_id = _request_id(request)
    try:
        claims = _authenticate_claims(request)
        _require_admin(claims)
        data = {"db": db, "table": table, "columns": list_columns(db, table)}
        return _success_response(data, request_id)
    except (SQLGWAdminError, SQLGWSchemaError) as exc:
        return _error_response(exc.code, exc.message, exc.status_code, request_id)


@router.get("/policies")
async def get_policies(request: Request, limit: int = Query(20, ge=1, le=100)):
    request_id = _request_id(request)
    try:
        claims = _authenticate_claims(request)
        _require_admin(claims)
        data = {"items": list_policy_versions(limit=limit)}
        return _success_response(data, request_id)
    except (SQLGWAdminError, SQLGWPolicyError) as exc:
        return _error_response(exc.code, exc.message, exc.status_code, request_id)


@router.get("/policies/{policy_id}")
async def get_policy(request: Request, policy_id: int):
    request_id = _request_id(request)
    try:
        claims = _authenticate_claims(request)
        _require_admin(claims)
        data = {"policy": get_policy_version(policy_id)}
        return _success_response(data, request_id)
    except (SQLGWAdminError, SQLGWPolicyError) as exc:
        return _error_response(exc.code, exc.message, exc.status_code, request_id)


@router.post("/policies")
async def create_policy(request: Request, payload: PolicyCreateRequest):
    request_id = _request_id(request)
    try:
        claims = _authenticate_claims(request)
        _require_admin(claims)

        created = create_policy_draft(
            policy_json=payload.policy_json,
            created_by=_subject(claims),
            notes=payload.notes,
            validate_schema=payload.validate_schema,
        )

        logger.info(
            "SQLGW_POLICY_AUDIT request_id=%s sub=%s action=create_draft version=%s result=success",
            request_id,
            _subject(claims),
            created.get("version"),
        )

        return _success_response({"policy": created}, request_id)
    except (SQLGWAdminError, SQLGWPolicyError) as exc:
        return _error_response(exc.code, exc.message, exc.status_code, request_id)


@router.post("/policies/{policy_id}/approve")
async def approve_policy_endpoint(request: Request, policy_id: int):
    request_id = _request_id(request)
    try:
        claims = _authenticate_claims(request)
        _require_approver(claims)
        approved = approve_policy(policy_id, approved_by=_subject(claims))

        logger.info(
            "SQLGW_POLICY_AUDIT request_id=%s sub=%s action=approve version=%s result=success",
            request_id,
            _subject(claims),
            approved.get("version"),
        )

        return _success_response({"policy": approved}, request_id)
    except (SQLGWAdminError, SQLGWPolicyError) as exc:
        return _error_response(exc.code, exc.message, exc.status_code, request_id)


@router.post("/policies/{policy_id}/activate")
async def activate_policy_endpoint(request: Request, policy_id: int):
    request_id = _request_id(request)
    try:
        claims = _authenticate_claims(request)
        _require_approver(claims)
        activated = activate_policy(policy_id, activated_by=_subject(claims))

        logger.info(
            "SQLGW_POLICY_AUDIT request_id=%s sub=%s action=activate version=%s result=success",
            request_id,
            _subject(claims),
            activated.get("version"),
        )

        return _success_response({"policy": activated}, request_id)
    except (SQLGWAdminError, SQLGWPolicyError) as exc:
        return _error_response(exc.code, exc.message, exc.status_code, request_id)


@router.post("/policies/{policy_id}/archive")
async def archive_policy_endpoint(request: Request, policy_id: int):
    request_id = _request_id(request)
    try:
        claims = _authenticate_claims(request)
        _require_approver(claims)
        archived = archive_policy(policy_id, archived_by=_subject(claims))

        logger.info(
            "SQLGW_POLICY_AUDIT request_id=%s sub=%s action=archive version=%s result=success",
            request_id,
            _subject(claims),
            archived.get("version"),
        )

        return _success_response({"policy": archived}, request_id)
    except (SQLGWAdminError, SQLGWPolicyError) as exc:
        return _error_response(exc.code, exc.message, exc.status_code, request_id)

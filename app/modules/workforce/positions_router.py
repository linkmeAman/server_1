"""Employee Position management endpoints.

Prefix: /positions (nested under /api/workforce from parent router)
Final paths:
  GET    /api/workforce/positions
  GET    /api/workforce/positions/departments          -- dropdown meta
  GET    /api/workforce/positions/{id}
  POST   /api/workforce/positions
  PATCH  /api/workforce/positions/{id}
  DELETE /api/workforce/positions/{id}
  GET    /api/workforce/positions/{id}/permissions
  PUT    /api/workforce/positions/{id}/permissions
  POST   /api/workforce/positions/{id}/permissions/copy
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_central_db_session, get_main_db_session
from app.core.prism_pdp import PDPRequest, evaluate
from app.core.response import success_response

from .dependencies import CallerContext, require_any_caller

router = APIRouter(prefix="/positions", tags=["workforce-positions"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POSITION_NAME_RE = re.compile(r"^[\w\s\-&/.()]+$")


def _row(result) -> dict | None:  # type: ignore[return]
    row = result.fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def _rows(result) -> list[dict]:  # type: ignore[return]
    return [dict(r._mapping) for r in result.fetchall()]


async def _get_client_id(caller: CallerContext, central_db: AsyncSession) -> int | None:
    """Look up client_id from the user table in central DB.
    Returns None for super admins (auth_supreme_user IDs differ from user.id).
    """
    if caller.is_super:
        return None
    row = _row(
        await central_db.execute(
            text(
                "SELECT client_id FROM user "
                "WHERE id = :uid AND (park = 0 OR park IS NULL) LIMIT 1"
            ),
            {"uid": caller.user_id},
        )
    )
    if not row or not row.get("client_id"):
        raise HTTPException(status_code=403, detail="No client context found for user")
    return int(row["client_id"])


async def _check_prism(
    caller: CallerContext,
    action: str,
    central_db: AsyncSession,
    resource_id: str | None = None,
) -> None:
    """Gate a write action via PRISM PDP unless caller is super-admin."""
    if caller.is_super:
        return
    pdp = await evaluate(
        PDPRequest(
            user_id=caller.user_id,
            action=action,
            resource_type="employee_position",
            resource_id=resource_id,
        ),
        central_db,
    )
    if pdp.decision != "Allow":
        raise HTTPException(status_code=403, detail=f"PRISM: Not authorized — {action}")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class GradeInput(BaseModel):
    grade: int = Field(..., ge=1)
    name: str = Field(..., min_length=1, max_length=100)
    salary: float = Field(default=0.0, ge=0)
    description: str | None = Field(default=None, max_length=500)
    assign_all_branch: int = Field(default=0)

    @field_validator("assign_all_branch")
    @classmethod
    def validate_aab(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("assign_all_branch must be 0 or 1")
        return v


class PositionCreateRequest(BaseModel):
    position: str = Field(..., min_length=1, max_length=150)
    department_id: int = Field(..., ge=1)
    description: str | None = Field(default=None, max_length=500)
    grades: list[GradeInput] = Field(..., min_length=1)

    @field_validator("position")
    @classmethod
    def sanitize_position(cls, v: str) -> str:
        v = v.strip()
        if not _POSITION_NAME_RE.match(v):
            raise ValueError("position contains invalid characters")
        return v

    @field_validator("grades")
    @classmethod
    def validate_grade_seq(cls, v: list[GradeInput]) -> list[GradeInput]:
        expected = list(range(1, len(v) + 1))
        actual = sorted(g.grade for g in v)
        if actual != expected:
            raise ValueError("grade numbers must be sequential starting from 1")
        return v


class PositionUpdateRequest(BaseModel):
    position: str | None = Field(default=None, min_length=1, max_length=150)
    department_id: int | None = Field(default=None, ge=1)
    description: str | None = Field(default=None, max_length=500)
    grades: list[GradeInput] | None = Field(default=None, min_length=1)

    @field_validator("position")
    @classmethod
    def sanitize_position(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not _POSITION_NAME_RE.match(v):
            raise ValueError("position contains invalid characters")
        return v

    @field_validator("grades")
    @classmethod
    def validate_grade_seq(cls, v: list[GradeInput] | None) -> list[GradeInput] | None:
        if v is None:
            return None
        expected = list(range(1, len(v) + 1))
        actual = sorted(g.grade for g in v)
        if actual != expected:
            raise ValueError("grade numbers must be sequential starting from 1")
        return v


class PermissionRow(BaseModel):
    module_id: int = Field(..., ge=1)
    permission: int = Field(..., ge=0, le=5)


class BulkPermissionsRequest(BaseModel):
    grade: int = Field(..., ge=1)
    permissions: list[PermissionRow] = Field(..., min_length=1)


class CopyGradeRequest(BaseModel):
    from_grade: int = Field(..., ge=1)
    to_grade: int = Field(..., ge=1)

    @field_validator("to_grade")
    @classmethod
    def grades_differ(cls, v: int, info) -> int:
        if info.data.get("from_grade") == v:
            raise ValueError("from_grade and to_grade must differ")
        return v


# ---------------------------------------------------------------------------
# Endpoints — list & meta
# ---------------------------------------------------------------------------


@router.get("/departments")
async def list_position_departments(
    _: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Dropdown options: all active departments."""
    rows = _rows(
        await central_db.execute(
            text(
                "SELECT id, department AS name "
                "FROM employee_department "
                "WHERE park = 0 OR park IS NULL "
                "ORDER BY department"
            )
        )
    )
    return success_response(data=rows, message="Departments fetched").model_dump(mode="json")


@router.get("")
async def list_positions(
    q: Annotated[str | None, Query(max_length=100)] = None,
    department_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Paginated position list from employee_position_view (main DB)."""
    if not caller.is_super:
        pdp = await evaluate(
            PDPRequest(
                user_id=caller.user_id,
                action="employee_position:read",
                resource_type="employee_position",
            ),
            central_db,
        )
        if pdp.decision != "Allow":
            raise HTTPException(status_code=403, detail="PRISM: Not authorized to list positions")

    conditions: list[str] = ["(epv.park = 0 OR epv.park IS NULL)"]
    params: dict = {"limit": limit, "offset": offset}

    if q:
        conditions.append("epv.position LIKE :q")
        params["q"] = f"%{q.strip()}%"
    if department_id:
        conditions.append("epv.department_id = :department_id")
        params["department_id"] = department_id

    where = "WHERE " + " AND ".join(conditions)

    rows = _rows(
        await main_db.execute(
            text(
                f"""
                SELECT
                    epv.id,
                    epv.position,
                    epv.department_id,
                    epv.department_name AS department,
                    epv.grade_count,
                    epv.description,
                    COALESCE(epv.employee_count, 0) AS employee_count,
                    COALESCE(epv.apply, 0)           AS apply
                FROM employee_position_view epv
                {where}
                ORDER BY epv.position
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total_row = _row(
        await main_db.execute(
            text(f"SELECT COUNT(*) AS cnt FROM employee_position_view epv {where}"),
            count_params,
        )
    )
    total = int((total_row or {}).get("cnt", 0))

    return success_response(
        data={"positions": rows, "total": total},
        message="Positions fetched",
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Endpoints — single position detail
# ---------------------------------------------------------------------------


@router.get("/{position_id}")
async def get_position(
    position_id: int,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Position detail including grade list (from pf_central)."""
    if not caller.is_super:
        await _check_prism(caller, "employee_position:read", central_db, str(position_id))

    pos = _row(
        await central_db.execute(
            text(
                "SELECT id, position, department_id, grade_count, description "
                "FROM employee_position "
                "WHERE id = :id AND (park = 0 OR park IS NULL) "
                "LIMIT 1"
            ),
            {"id": position_id},
        )
    )
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    grades = _rows(
        await central_db.execute(
            text(
                "SELECT id, grade, name, salary, assign_all_branch, description "
                "FROM grade "
                "WHERE pos_id = :pos_id "
                "ORDER BY grade"
            ),
            {"pos_id": position_id},
        )
    )

    return success_response(
        data={**pos, "grades": grades},
        message="Position fetched",
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Endpoints — create
# ---------------------------------------------------------------------------


@router.post("")
async def create_position(
    body: PositionCreateRequest,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "employee_position:create", central_db)

    # Duplicate name check
    existing = _row(
        await central_db.execute(
            text(
                "SELECT id FROM employee_position "
                "WHERE position = :name AND (park = 0 OR park IS NULL) LIMIT 1"
            ),
            {"name": body.position},
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="A position with this name already exists")

    grade_count = len(body.grades)

    result = await central_db.execute(
        text(
            "INSERT INTO employee_position (position, department_id, grade_count, description, created_by, park) "
            "VALUES (:position, :department_id, :grade_count, :description, :created_by, 0)"
        ),
        {
            "position": body.position,
            "department_id": body.department_id,
            "grade_count": grade_count,
            "description": body.description,
            "created_by": caller.user_id,
        },
    )
    position_id = result.lastrowid  # type: ignore[attr-defined]

    for g in body.grades:
        await central_db.execute(
            text(
                "INSERT INTO grade (pos_id, grade, name, salary, assign_all_branch, description, created_by) "
                "VALUES (:pos_id, :grade, :name, :salary, :aab, :description, :created_by)"
            ),
            {
                "pos_id": position_id,
                "grade": g.grade,
                "name": g.name,
                "salary": g.salary,
                "aab": g.assign_all_branch,
                "description": g.description,
                "created_by": caller.user_id,
            },
        )

    await central_db.commit()

    return success_response(
        data={"id": position_id},
        message="Position created",
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Endpoints — update
# ---------------------------------------------------------------------------


@router.patch("/{position_id}")
async def update_position(
    position_id: int,
    body: PositionUpdateRequest,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "employee_position:update", central_db, str(position_id))

    # Confirm exists
    pos = _row(
        await central_db.execute(
            text(
                "SELECT id, grade_count FROM employee_position "
                "WHERE id = :id AND (park = 0 OR park IS NULL) LIMIT 1"
            ),
            {"id": position_id},
        )
    )
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    # Duplicate name guard (exclude self)
    if body.position:
        dup = _row(
            await central_db.execute(
                text(
                    "SELECT id FROM employee_position "
                    "WHERE position = :name AND id != :id AND (park = 0 OR park IS NULL) LIMIT 1"
                ),
                {"name": body.position, "id": position_id},
            )
        )
        if dup:
            raise HTTPException(status_code=409, detail="Another position with this name already exists")

    # Build SET clause from provided fields
    set_parts: list[str] = []
    update_params: dict = {"id": position_id}

    if body.position is not None:
        set_parts.append("position = :position")
        update_params["position"] = body.position
    if body.department_id is not None:
        set_parts.append("department_id = :department_id")
        update_params["department_id"] = body.department_id
    if body.description is not None:
        set_parts.append("description = :description")
        update_params["description"] = body.description
    if body.grades is not None:
        set_parts.append("grade_count = :grade_count")
        update_params["grade_count"] = len(body.grades)

    if set_parts:
        await central_db.execute(
            text(f"UPDATE employee_position SET {', '.join(set_parts)} WHERE id = :id"),
            update_params,
        )

    # Replace grade rows if provided
    if body.grades is not None:
        await central_db.execute(
            text("DELETE FROM grade WHERE pos_id = :pos_id"),
            {"pos_id": position_id},
        )
        for g in body.grades:
            await central_db.execute(
                text(
                    "INSERT INTO grade (pos_id, grade, name, salary, assign_all_branch, description, created_by) "
                    "VALUES (:pos_id, :grade, :name, :salary, :aab, :description, :created_by)"
                ),
                {
                    "pos_id": position_id,
                    "grade": g.grade,
                    "name": g.name,
                    "salary": g.salary,
                    "aab": g.assign_all_branch,
                    "description": g.description,
                    "created_by": caller.user_id,
                },
            )

    await central_db.commit()

    return success_response(data={"id": position_id}, message="Position updated").model_dump(mode="json")


# ---------------------------------------------------------------------------
# Endpoints — soft delete
# ---------------------------------------------------------------------------


@router.delete("/{position_id}")
async def delete_position(
    position_id: int,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "employee_position:delete", central_db, str(position_id))

    pos = _row(
        await central_db.execute(
            text(
                "SELECT id FROM employee_position "
                "WHERE id = :id AND (park = 0 OR park IS NULL) LIMIT 1"
            ),
            {"id": position_id},
        )
    )
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    await central_db.execute(
        text("UPDATE employee_position SET park = 1 WHERE id = :id"),
        {"id": position_id},
    )
    await central_db.commit()

    return success_response(data={"id": position_id}, message="Position deleted").model_dump(mode="json")


# ---------------------------------------------------------------------------
# Endpoints — employees at a position
# ---------------------------------------------------------------------------


@router.get("/{position_id}/employees")
async def get_position_employees(
    position_id: int,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Return all active employees assigned to this position."""
    if not caller.is_super:
        pdp = await evaluate(
            PDPRequest(
                user_id=caller.user_id,
                action="employee:read",
                resource_type="employee",
            ),
            central_db,
        )
        if pdp.decision != "Allow":
            raise HTTPException(status_code=403, detail="PRISM: Not authorized to view employees")

    rows = _rows(
        await main_db.execute(
            text(
                """
                SELECT
                    e.id                                                   AS emp_id,
                    e.contact_id,
                    e.grade,
                    CASE WHEN e.is_admin = 1 THEN 'Yes' ELSE 'No' END     AS has_admin_role,
                    TRIM(CONCAT(c.fname, ' ', COALESCE(c.lname, '')))      AS full_name,
                    c.mobile,
                    c.email
                FROM employee e
                JOIN contact c ON c.id = e.contact_id
                WHERE e.position_id = :position_id
                  AND (e.park = 0 OR e.park IS NULL)
                ORDER BY e.grade, c.fname
                """
            ),
            {"position_id": position_id},
        )
    )

    return success_response(
        data={"employees": rows, "total": len(rows)},
        message="Position employees fetched",
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Endpoint — toggle apply (Trigger)
# ---------------------------------------------------------------------------


@router.patch("/{position_id}/toggle-apply")
async def toggle_position_apply(
    position_id: int,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Toggle the `apply` flag (0↔1) — activates or deactivates the position template."""
    await _check_prism(caller, "employee_position:update", central_db, str(position_id))

    pos = _row(
        await central_db.execute(
            text(
                "SELECT id, apply FROM employee_position "
                "WHERE id = :id AND (park = 0 OR park IS NULL) LIMIT 1"
            ),
            {"id": position_id},
        )
    )
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    new_apply = 0 if pos.get("apply") else 1
    await central_db.execute(
        text("UPDATE employee_position SET apply = :apply WHERE id = :id"),
        {"apply": new_apply, "id": position_id},
    )
    await central_db.commit()

    return success_response(
        data={"id": position_id, "apply": new_apply},
        message="Position activated" if new_apply else "Position deactivated",
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Endpoints — permissions
# ---------------------------------------------------------------------------


@router.get("/{position_id}/permissions")
async def get_position_permissions(
    position_id: int,
    grade: int = Query(..., ge=1),
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Return all active client modules with permission level for the given position+grade.
    Defaults to 0 (No Access) when no position_template row exists.
    """
    if not caller.is_super:
        await _check_prism(caller, "employee_position:manage_permissions", central_db, str(position_id))

    client_id = await _get_client_id(caller, central_db)

    # Confirm position exists
    pos = _row(
        await central_db.execute(
            text(
                "SELECT id, grade_count FROM employee_position "
                "WHERE id = :id AND (park = 0 OR park IS NULL) LIMIT 1"
            ),
            {"id": position_id},
        )
    )
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    if grade > int(pos["grade_count"]):
        raise HTTPException(
            status_code=422,
            detail=f"Grade {grade} exceeds position's grade_count ({pos['grade_count']})",
        )

    if client_id is not None:
        client_filter_sql = "WHERE cm.client_id = :client_id AND cm.active = 1"
        query_params: dict = {"position_id": position_id, "grade": grade, "client_id": client_id}
    else:
        # Super admin — show all active modules, deduplicated across clients
        client_filter_sql = "WHERE cm.active = 1"
        query_params = {"position_id": position_id, "grade": grade}

    rows = _rows(
        await central_db.execute(
            text(
                f"""
                SELECT
                    MIN(cm.id)     AS client_module_id,
                    cm.module_id,
                    cm.module      AS module_name,
                    COALESCE(MAX(pt.permission), 0) AS permission
                FROM client_module cm
                LEFT JOIN position_template pt
                    ON pt.module_id = cm.module_id
                    AND pt.epos_id  = :position_id
                    AND pt.grade    = :grade
                {client_filter_sql}
                GROUP BY cm.module_id, cm.module
                ORDER BY cm.module
                """
            ),
            query_params,
        )
    )

    return success_response(
        data={
            "position_id": position_id,
            "grade": grade,
            "grade_count": int(pos["grade_count"]),
            "modules": rows,
        },
        message="Permissions fetched",
    ).model_dump(mode="json")


@router.put("/{position_id}/permissions")
async def put_position_permissions(
    position_id: int,
    body: BulkPermissionsRequest,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Upsert permission rows for a position+grade in bulk."""
    await _check_prism(caller, "employee_position:manage_permissions", central_db, str(position_id))

    # Confirm position and grade validity
    pos = _row(
        await central_db.execute(
            text(
                "SELECT id, grade_count FROM employee_position "
                "WHERE id = :id AND (park = 0 OR park IS NULL) LIMIT 1"
            ),
            {"id": position_id},
        )
    )
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    if body.grade > int(pos["grade_count"]):
        raise HTTPException(
            status_code=422,
            detail=f"Grade {body.grade} exceeds position's grade_count ({pos['grade_count']})",
        )

    for perm in body.permissions:
        await central_db.execute(
            text(
                """
                INSERT INTO position_template (epos_id, module_id, permission, grade)
                VALUES (:epos_id, :module_id, :permission, :grade)
                ON DUPLICATE KEY UPDATE permission = :permission
                """
            ),
            {
                "epos_id": position_id,
                "module_id": perm.module_id,
                "permission": perm.permission,
                "grade": body.grade,
            },
        )

    await central_db.commit()

    return success_response(
        data={"updated": len(body.permissions)},
        message="Permissions saved",
    ).model_dump(mode="json")


@router.post("/{position_id}/permissions/copy")
async def copy_grade_permissions(
    position_id: int,
    body: CopyGradeRequest,
    caller: CallerContext = Depends(require_any_caller),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    """Copy all permission_template rows from one grade to another for this position.
    Destination grade rows are deleted first (clean copy).
    """
    await _check_prism(caller, "employee_position:manage_permissions", central_db, str(position_id))

    pos = _row(
        await central_db.execute(
            text(
                "SELECT id, grade_count FROM employee_position "
                "WHERE id = :id AND (park = 0 OR park IS NULL) LIMIT 1"
            ),
            {"id": position_id},
        )
    )
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    grade_count = int(pos["grade_count"])
    if body.from_grade > grade_count or body.to_grade > grade_count:
        raise HTTPException(
            status_code=422,
            detail=f"Grade must be between 1 and {grade_count}",
        )

    # Delete destination rows
    await central_db.execute(
        text(
            "DELETE FROM position_template "
            "WHERE epos_id = :pos_id AND grade = :to_grade"
        ),
        {"pos_id": position_id, "to_grade": body.to_grade},
    )

    # Copy from source
    await central_db.execute(
        text(
            """
            INSERT INTO position_template (epos_id, module_id, permission, grade)
            SELECT epos_id, module_id, permission, :to_grade
            FROM position_template
            WHERE epos_id = :pos_id AND grade = :from_grade
            """
        ),
        {
            "pos_id": position_id,
            "from_grade": body.from_grade,
            "to_grade": body.to_grade,
        },
    )
    await central_db.commit()

    return success_response(
        data={"from_grade": body.from_grade, "to_grade": body.to_grade},
        message=f"Grade {body.from_grade} permissions copied to grade {body.to_grade}",
    ).model_dump(mode="json")

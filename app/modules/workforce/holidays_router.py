"""Workforce holiday management endpoints.

Prefix: /holidays (nested under /api/workforce)
Final paths:
  GET    /api/workforce/holidays
  GET    /api/workforce/holidays/{id}
  POST   /api/workforce/holidays
  PATCH  /api/workforce/holidays/{id}
  DELETE /api/workforce/holidays/{id}
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_central_db_session, get_main_db_session
from app.core.prism_pdp import PDPRequest, evaluate
from app.core.response import success_response

from .dependencies import CallerContext, require_any_caller

router = APIRouter(prefix="/holidays", tags=["workforce-holidays"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(result) -> dict | None:  # type: ignore[return]
    row = result.fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def _rows(result) -> list[dict]:  # type: ignore[return]
    return [dict(r._mapping) for r in result.fetchall()]


async def _check_prism(
    caller: CallerContext,
    action: str,
    central_db: AsyncSession,
    resource_id: str | None = None,
) -> None:
    if caller.is_super:
        return
    pdp = await evaluate(
        PDPRequest(
            user_id=caller.user_id,
            action=action,
            resource_type="workforce_holiday",
            resource_id=resource_id,
        ),
        central_db,
    )
    if pdp.decision != "Allow":
        raise HTTPException(status_code=403, detail=f"PRISM: Not authorized - {action}")


def _serialize_holiday(row: dict) -> dict:
    holiday_date = row.get("date")
    return {
        "id": int(row.get("id") or 0),
        "date": holiday_date.isoformat() if holiday_date is not None else None,
        "title": (row.get("title") or "").strip(),
        "every_year": int(row.get("every_year") or 0),
        "national": int(row.get("national") or 0),
        "for_faculty": int(row.get("for_faculty") or 0),
        "for_employee": int(row.get("for_employee") or 0),
        "meraki": int(row.get("meraki") or 0),
        "park": int(row.get("park") or 0),
    }


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class HolidayCreateRequest(BaseModel):
    date: str = Field(..., min_length=10, max_length=10)
    title: str = Field(..., min_length=1, max_length=100)
    every_year: int = Field(default=0)
    national: int = Field(default=0)
    for_faculty: int = Field(default=0)
    for_employee: int = Field(default=0)
    meraki: int = Field(default=0)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title is required")
        return cleaned

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        cleaned = value.strip()
        try:
            datetime.strptime(cleaned, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("date must be YYYY-MM-DD") from exc
        return cleaned

    @field_validator("every_year", "national", "for_faculty", "for_employee", "meraki")
    @classmethod
    def validate_flags(cls, value: int) -> int:
        if value not in (0, 1):
            raise ValueError("flag must be 0 or 1")
        return value


class HolidayUpdateRequest(BaseModel):
    date: str | None = Field(default=None, min_length=10, max_length=10)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    every_year: int | None = Field(default=None)
    national: int | None = Field(default=None)
    for_faculty: int | None = Field(default=None)
    for_employee: int | None = Field(default=None)
    meraki: int | None = Field(default=None)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title cannot be empty")
        return cleaned

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        try:
            datetime.strptime(cleaned, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("date must be YYYY-MM-DD") from exc
        return cleaned

    @field_validator("every_year", "national", "for_faculty", "for_employee", "meraki")
    @classmethod
    def validate_flags(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value not in (0, 1):
            raise ValueError("flag must be 0 or 1")
        return value


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_holidays(
    q: str | None = Query(default=None),
    year: int | None = Query(default=None, ge=2000, le=2100),
    national: int | None = Query(default=None, ge=0, le=1),
    for_employee: int | None = Query(default=None, ge=0, le=1),
    for_faculty: int | None = Query(default=None, ge=0, le=1),
    meraki: int | None = Query(default=None, ge=0, le=1),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "workforce:holiday:read", central_db)

    conditions = ["h.park = 0"]
    params: dict[str, object] = {"limit": limit, "offset": offset}

    if q:
        conditions.append("h.title LIKE :q")
        params["q"] = f"%{q.strip()}%"
    if year is not None:
        conditions.append("YEAR(h.`date`) = :year")
        params["year"] = int(year)
    if national is not None:
        conditions.append("h.national = :national")
        params["national"] = int(national)
    if for_employee is not None:
        conditions.append("h.for_employee = :for_employee")
        params["for_employee"] = int(for_employee)
    if for_faculty is not None:
        conditions.append("h.for_faculty = :for_faculty")
        params["for_faculty"] = int(for_faculty)
    if meraki is not None:
        conditions.append("h.meraki = :meraki")
        params["meraki"] = int(meraki)

    where_clause = "WHERE " + " AND ".join(conditions)

    total_row = _row(
        await main_db.execute(
            text(f"SELECT COUNT(*) AS total FROM holiday h {where_clause}"),
            params,
        )
    )
    total = int(total_row.get("total") or 0) if total_row else 0

    rows = _rows(
        await main_db.execute(
            text(
                f"""
                SELECT
                    h.id,
                    h.`date`,
                    h.title,
                    h.every_year,
                    h.national,
                    h.for_faculty,
                    h.for_employee,
                    h.meraki,
                    h.park
                FROM holiday h
                {where_clause}
                ORDER BY h.`date` DESC, h.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )

    return success_response(
        data={
            "holidays": [_serialize_holiday(row) for row in rows],
            "total": total,
        },
        message="Holidays fetched",
    ).model_dump(mode="json")


@router.get("/{holiday_id}")
async def get_holiday(
    holiday_id: int,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "workforce:holiday:read", central_db, str(holiday_id))

    row = _row(
        await main_db.execute(
            text(
                """
                SELECT
                    h.id,
                    h.`date`,
                    h.title,
                    h.every_year,
                    h.national,
                    h.for_faculty,
                    h.for_employee,
                    h.meraki,
                    h.park
                FROM holiday h
                WHERE h.id = :holiday_id AND h.park = 0
                LIMIT 1
                """
            ),
            {"holiday_id": int(holiday_id)},
        )
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Holiday not found")

    return success_response(
        data=_serialize_holiday(row),
        message="Holiday fetched",
    ).model_dump(mode="json")


@router.post("")
async def create_holiday(
    payload: HolidayCreateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "workforce:holiday:create", central_db)

    existing = _row(
        await main_db.execute(
            text(
                """
                SELECT id
                FROM holiday
                WHERE `date` = :date
                  AND title = :title
                  AND park = 0
                LIMIT 1
                """
            ),
            {"date": payload.date, "title": payload.title},
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Holiday already exists for this date")

    result = await main_db.execute(
        text(
            """
            INSERT INTO holiday (
                `date`,
                title,
                every_year,
                national,
                for_faculty,
                for_employee,
                meraki,
                park,
                created_at,
                created_by,
                modified_at,
                modified_by
            ) VALUES (
                :date,
                :title,
                :every_year,
                :national,
                :for_faculty,
                :for_employee,
                :meraki,
                0,
                NOW(),
                :created_by,
                NOW(),
                :modified_by
            )
            """
        ),
        {
            "date": payload.date,
            "title": payload.title,
            "every_year": int(payload.every_year),
            "national": int(payload.national),
            "for_faculty": int(payload.for_faculty),
            "for_employee": int(payload.for_employee),
            "meraki": int(payload.meraki),
            "created_by": int(caller.user_id),
            "modified_by": int(caller.user_id),
        },
    )

    holiday_id = int(getattr(result, "lastrowid", None) or 0)
    if holiday_id <= 0:
        raise HTTPException(status_code=500, detail="Failed to create holiday")

    await main_db.commit()

    return success_response(
        data={"id": holiday_id},
        message="Holiday created",
    ).model_dump(mode="json")


@router.patch("/{holiday_id}")
async def update_holiday(
    holiday_id: int,
    payload: HolidayUpdateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "workforce:holiday:update", central_db, str(holiday_id))

    existing = _row(
        await main_db.execute(
            text("SELECT id FROM holiday WHERE id = :holiday_id AND park = 0 LIMIT 1"),
            {"holiday_id": int(holiday_id)},
        )
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Holiday not found")

    assignments: list[str] = []
    params: dict[str, object] = {
        "holiday_id": int(holiday_id),
        "modified_by": int(caller.user_id),
    }

    if payload.date is not None:
        assignments.append("`date` = :date")
        params["date"] = payload.date
    if payload.title is not None:
        assignments.append("title = :title")
        params["title"] = payload.title
    if payload.every_year is not None:
        assignments.append("every_year = :every_year")
        params["every_year"] = int(payload.every_year)
    if payload.national is not None:
        assignments.append("national = :national")
        params["national"] = int(payload.national)
    if payload.for_faculty is not None:
        assignments.append("for_faculty = :for_faculty")
        params["for_faculty"] = int(payload.for_faculty)
    if payload.for_employee is not None:
        assignments.append("for_employee = :for_employee")
        params["for_employee"] = int(payload.for_employee)
    if payload.meraki is not None:
        assignments.append("meraki = :meraki")
        params["meraki"] = int(payload.meraki)

    if not assignments:
        return success_response(
            data={"id": int(holiday_id)},
            message="No changes submitted",
        ).model_dump(mode="json")

    assignments.append("modified_at = NOW()")
    assignments.append("modified_by = :modified_by")

    await main_db.execute(
        text(
            f"""
            UPDATE holiday
            SET {", ".join(assignments)}
            WHERE id = :holiday_id AND park = 0
            """
        ),
        params,
    )
    await main_db.commit()

    return success_response(
        data={"id": int(holiday_id)},
        message="Holiday updated",
    ).model_dump(mode="json")


@router.delete("/{holiday_id}")
async def delete_holiday(
    holiday_id: int,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "workforce:holiday:delete", central_db, str(holiday_id))

    existing = _row(
        await main_db.execute(
            text("SELECT id FROM holiday WHERE id = :holiday_id AND park = 0 LIMIT 1"),
            {"holiday_id": int(holiday_id)},
        )
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Holiday not found")

    await main_db.execute(
        text(
            """
            UPDATE holiday
            SET park = 1,
                modified_at = NOW(),
                modified_by = :modified_by
            WHERE id = :holiday_id
            """
        ),
        {
            "holiday_id": int(holiday_id),
            "modified_by": int(caller.user_id),
        },
    )
    await main_db.commit()

    return success_response(
        data={"id": int(holiday_id)},
        message="Holiday deleted",
    ).model_dump(mode="json")

"""Workshift management endpoints.

Prefix: /workshifts (nested under /api/workforce)
Final paths:
  GET    /api/workforce/workshifts
  GET    /api/workforce/workshifts/{id}
  POST   /api/workforce/workshifts
  PATCH  /api/workforce/workshifts/{id}
"""

from __future__ import annotations

from datetime import datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_central_db_session, get_main_db_session
from app.core.prism_pdp import PDPRequest, evaluate
from app.core.response import success_response

from .dependencies import CallerContext, require_any_caller

router = APIRouter(prefix="/workshifts", tags=["workforce-workshifts"])

_TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p")

_DAY_LABELS = {
    "sun": ("Sun", 0),
    "sunday": ("Sun", 0),
    "mon": ("Mon", 1),
    "monday": ("Mon", 1),
    "tue": ("Tue", 2),
    "tues": ("Tue", 2),
    "tuesday": ("Tue", 2),
    "wed": ("Wed", 3),
    "wednesday": ("Wed", 3),
    "thu": ("Thu", 4),
    "thur": ("Thu", 4),
    "thurs": ("Thu", 4),
    "thursday": ("Thu", 4),
    "fri": ("Fri", 5),
    "friday": ("Fri", 5),
    "sat": ("Sat", 6),
    "saturday": ("Sat", 6),
}

_DAY_CODE_TO_LABEL = {
    0: "Sun",
    1: "Mon",
    2: "Tue",
    3: "Wed",
    4: "Thu",
    5: "Fri",
    6: "Sat",
}

_WORKSHIFT_DAY_COLUMNS: set[str] | None = None


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


def _normalize_day_label(value: str) -> str:
    key = value.strip().lower()
    if key in _DAY_LABELS:
        return _DAY_LABELS[key][0]
    raise ValueError("day must be a valid weekday")


def _day_code_from_label(label: str | None) -> int | None:
    if not label:
        return None
    key = label.strip().lower()
    if key in _DAY_LABELS:
        return _DAY_LABELS[key][1]
    return None


def _day_label_from_code(code: int | None) -> str | None:
    if code is None:
        return None
    return _DAY_CODE_TO_LABEL.get(int(code))


def _normalize_time(value: str, field: str) -> str:
    cleaned = value.strip()
    for fmt in _TIME_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.strftime("%H:%M:%S")
        except ValueError:
            continue
    raise HTTPException(status_code=422, detail=f"{field} must be a valid time")


def _normalize_time_or_default(value: str | None, field: str, default: str | None) -> str | None:
    if value is None:
        return default
    cleaned = value.strip()
    if not cleaned:
        return default
    return _normalize_time(cleaned, field)


def _format_time_hhmm(value: str | time | None) -> str:
    if value is None:
        return ""
    if isinstance(value, time):
        return value.strftime("%H:%M")
    text_value = str(value).strip()
    if not text_value:
        return ""
    if ":" in text_value:
        parts = text_value.split(":")
        if len(parts) >= 2:
            return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
    for fmt in _TIME_FORMATS:
        try:
            parsed = datetime.strptime(text_value, fmt)
            return parsed.strftime("%H:%M")
        except ValueError:
            continue
    return text_value


async def _get_workshift_day_columns(db: AsyncSession) -> set[str]:
    global _WORKSHIFT_DAY_COLUMNS
    if _WORKSHIFT_DAY_COLUMNS is not None:
        return _WORKSHIFT_DAY_COLUMNS
    try:
        result = await db.execute(
            text(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workshift_day'
                """
            )
        )
        _WORKSHIFT_DAY_COLUMNS = {row._mapping["COLUMN_NAME"] for row in result.fetchall()}
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        _WORKSHIFT_DAY_COLUMNS = {"workshift_id", "day", "start_time", "end_time", "wfh"}
    return _WORKSHIFT_DAY_COLUMNS


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
            resource_type="workshift",
            resource_id=resource_id,
        ),
        central_db,
    )
    if pdp.decision != "Allow":
        raise HTTPException(status_code=403, detail=f"PRISM: Not authorized - {action}")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class WorkshiftDayInput(BaseModel):
    day: str | None = Field(default=None)
    day_code: int | None = Field(default=None, ge=0, le=6)
    start_time: str = Field(..., min_length=1, max_length=16)
    end_time: str = Field(..., min_length=1, max_length=16)
    wfh: int = Field(default=0)

    @field_validator("day")
    @classmethod
    def normalize_day(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _normalize_day_label(v)

    @field_validator("wfh")
    @classmethod
    def validate_wfh(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("wfh must be 0 or 1")
        return v

    @model_validator(mode="after")
    def ensure_day(self):
        if not self.day and self.day_code is None:
            raise ValueError("day or day_code is required")
        return self


class WorkshiftBreakInput(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    start_time: str = Field(..., min_length=1, max_length=16)
    end_time: str = Field(..., min_length=1, max_length=16)


class WorkshiftLateInput(BaseModel):
    late_by: str = Field(default="", max_length=16)
    deduct: str = Field(default="", max_length=10)
    late_deductions: int = Field(default=0)

    @field_validator("deduct")
    @classmethod
    def validate_deduct(cls, v: str) -> str:
        if v and v not in ("Full", "Half"):
            raise ValueError("deduct must be Full or Half")
        return v

    @field_validator("late_deductions")
    @classmethod
    def validate_late_deductions(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("late_deductions must be 0 or 1")
        return v


class WorkshiftPaidLeaveInput(BaseModel):
    leaves_addition: int = Field(default=0)
    paid_leaves_monthly: float = Field(default=0)

    @field_validator("leaves_addition")
    @classmethod
    def validate_leaves_addition(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("leaves_addition must be 0 or 1")
        return v

    @field_validator("paid_leaves_monthly")
    @classmethod
    def validate_paid_leaves(cls, v: float) -> float:
        if v < 0:
            raise ValueError("paid_leaves_monthly must be >= 0")
        if round(v * 2) != v * 2:
            raise ValueError("paid_leaves_monthly must be in 0.5 increments")
        if v > 9.5:
            raise ValueError("paid_leaves_monthly must be <= 9.5")
        return v


class WorkshiftNonAccrualLeaveInput(BaseModel):
    leaves_to_be_given: int = Field(default=0)
    non_accrual_leaves_monthly: float = Field(default=0)

    @field_validator("leaves_to_be_given")
    @classmethod
    def validate_leaves_to_be_given(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("leaves_to_be_given must be 0 or 1")
        return v

    @field_validator("non_accrual_leaves_monthly")
    @classmethod
    def validate_non_accrual(cls, v: float) -> float:
        if v < 0:
            raise ValueError("non_accrual_leaves_monthly must be >= 0")
        return v


class WorkshiftCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workshift: str = Field(..., min_length=1, max_length=150)
    first_last: int = Field(default=0)
    gracetime_early: str | None = Field(default=None)
    gracetime_late: str | None = Field(default=None)
    early_check_in: int = Field(default=0)
    early_check_in_time_deduction: str | None = Field(default=None, max_length=16)
    early_check_in_time: str | None = Field(default=None, max_length=16)
    inactive: int = Field(default=0)
    bid: int | None = Field(default=None)
    day: list[WorkshiftDayInput] = Field(default_factory=list)
    breaks: list[WorkshiftBreakInput] = Field(default_factory=list, alias="break")
    late: list[WorkshiftLateInput] = Field(default_factory=list)
    paid_leaves: list[WorkshiftPaidLeaveInput] = Field(default_factory=list)
    non_accrual_leaves: list[WorkshiftNonAccrualLeaveInput] = Field(default_factory=list)

    @field_validator("workshift")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("first_last", "early_check_in", "inactive")
    @classmethod
    def validate_flags(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("flag must be 0 or 1")
        return v


class WorkshiftUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workshift: str | None = Field(default=None, min_length=1, max_length=150)
    first_last: int | None = Field(default=None)
    gracetime_early: str | None = Field(default=None)
    gracetime_late: str | None = Field(default=None)
    early_check_in: int | None = Field(default=None)
    early_check_in_time_deduction: str | None = Field(default=None, max_length=16)
    early_check_in_time: str | None = Field(default=None, max_length=16)
    inactive: int | None = Field(default=None)
    bid: int | None = Field(default=None)
    day: list[WorkshiftDayInput] | None = None
    breaks: list[WorkshiftBreakInput] | None = Field(default=None, alias="break")
    late: list[WorkshiftLateInput] | None = None
    paid_leaves: list[WorkshiftPaidLeaveInput] | None = None
    non_accrual_leaves: list[WorkshiftNonAccrualLeaveInput] | None = None

    @field_validator("workshift")
    @classmethod
    def normalize_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip()

    @field_validator("first_last", "early_check_in", "inactive")
    @classmethod
    def validate_flags(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if v not in (0, 1):
            raise ValueError("flag must be 0 or 1")
        return v


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


async def _replace_workshift_days(
    db: AsyncSession,
    workshift_id: int,
    days: list[WorkshiftDayInput] | None,
) -> None:
    if days is None:
        return
    await db.execute(
        text("DELETE FROM workshift_day WHERE workshift_id = :workshift_id"),
        {"workshift_id": int(workshift_id)},
    )
    if not days:
        return

    columns = await _get_workshift_day_columns(db)
    insert_cols = ["workshift_id", "start_time", "end_time"]
    if "day" in columns:
        insert_cols.append("day")
    if "day_code" in columns:
        insert_cols.append("day_code")
    if "wfh" in columns:
        insert_cols.append("wfh")

    values_sql = ", ".join(f":{col}" for col in insert_cols)
    sql = text(
        f"INSERT INTO workshift_day ({', '.join(insert_cols)}) VALUES ({values_sql})"
    )

    for entry in days:
        day_label = entry.day
        day_code = entry.day_code
        if not day_label and day_code is not None:
            day_label = _day_label_from_code(day_code)
        if day_code is None and day_label:
            day_code = _day_code_from_label(day_label)

        params = {
            "workshift_id": int(workshift_id),
            "start_time": _normalize_time(entry.start_time, "day.start_time"),
            "end_time": _normalize_time(entry.end_time, "day.end_time"),
        }
        if "day" in insert_cols:
            params["day"] = day_label or ""
        if "day_code" in insert_cols:
            params["day_code"] = int(day_code) if day_code is not None else 0
        if "wfh" in insert_cols:
            params["wfh"] = int(entry.wfh)
        await db.execute(sql, params)


async def _replace_workshift_breaks(
    db: AsyncSession,
    workshift_id: int,
    breaks: list[WorkshiftBreakInput] | None,
) -> None:
    if breaks is None:
        return
    await db.execute(
        text("DELETE FROM workshift_break WHERE workshift_id = :workshift_id"),
        {"workshift_id": int(workshift_id)},
    )
    if not breaks:
        return

    sql = text(
        """
        INSERT INTO workshift_break (workshift_id, name, start_time, end_time)
        VALUES (:workshift_id, :name, :start_time, :end_time)
        """
    )
    for entry in breaks:
        params = {
            "workshift_id": int(workshift_id),
            "name": (entry.name or "").strip(),
            "start_time": _normalize_time(entry.start_time, "break.start_time"),
            "end_time": _normalize_time(entry.end_time, "break.end_time"),
        }
        await db.execute(sql, params)


async def _replace_workshift_lates(
    db: AsyncSession,
    workshift_id: int,
    lates: list[WorkshiftLateInput] | None,
) -> None:
    if lates is None:
        return
    await db.execute(
        text("DELETE FROM workshift_late WHERE workshift_id = :workshift_id"),
        {"workshift_id": int(workshift_id)},
    )
    if not lates:
        return

    sql = text(
        """
        INSERT INTO workshift_late (workshift_id, late_by, deduct, late_deductions)
        VALUES (:workshift_id, :late_by, :deduct, :late_deductions)
        """
    )
    for entry in lates:
        params = {
            "workshift_id": int(workshift_id),
            "late_by": entry.late_by.strip(),
            "deduct": entry.deduct,
            "late_deductions": int(entry.late_deductions),
        }
        await db.execute(sql, params)


async def _replace_workshift_paid_leaves(
    db: AsyncSession,
    workshift_id: int,
    paid_leaves: list[WorkshiftPaidLeaveInput] | None,
) -> None:
    if paid_leaves is None:
        return
    await db.execute(
        text("DELETE FROM workshift_paid_leaves WHERE workshift_id = :workshift_id"),
        {"workshift_id": int(workshift_id)},
    )
    if not paid_leaves:
        return

    sql = text(
        """
        INSERT INTO workshift_paid_leaves (workshift_id, leaves_addition, paid_leaves_monthly)
        VALUES (:workshift_id, :leaves_addition, :paid_leaves_monthly)
        """
    )
    for entry in paid_leaves:
        params = {
            "workshift_id": int(workshift_id),
            "leaves_addition": int(entry.leaves_addition),
            "paid_leaves_monthly": float(entry.paid_leaves_monthly),
        }
        await db.execute(sql, params)


async def _replace_workshift_non_accrual_leaves(
    db: AsyncSession,
    workshift_id: int,
    non_accrual_leaves: list[WorkshiftNonAccrualLeaveInput] | None,
) -> None:
    if non_accrual_leaves is None:
        return
    await db.execute(
        text("DELETE FROM workshift_non_accrual_leaves WHERE workshift_id = :workshift_id"),
        {"workshift_id": int(workshift_id)},
    )
    if not non_accrual_leaves:
        return

    sql = text(
        """
        INSERT INTO workshift_non_accrual_leaves (
            workshift_id, leaves_to_be_given, non_accrual_leaves_monthly
        ) VALUES (:workshift_id, :leaves_to_be_given, :non_accrual_leaves_monthly)
        """
    )
    for entry in non_accrual_leaves:
        params = {
            "workshift_id": int(workshift_id),
            "leaves_to_be_given": int(entry.leaves_to_be_given),
            "non_accrual_leaves_monthly": float(entry.non_accrual_leaves_monthly),
        }
        await db.execute(sql, params)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_workshifts(
    q: str | None = Query(default=None),
    inactive: int | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "workshift:read", central_db)

    conditions: list[str] = ["(w.park IS NULL OR w.park = 0)"]
    params: dict[str, object] = {"limit": limit, "offset": offset}

    if q:
        conditions.append("w.workshift LIKE :q")
        params["q"] = f"%{q.strip()}%"
    if inactive is not None:
        conditions.append("w.inactive = :inactive")
        params["inactive"] = int(inactive)

    where = "WHERE " + " AND ".join(conditions)

    total_row = _row(
        await main_db.execute(
            text(f"SELECT COUNT(*) AS total FROM workshift w {where}"),
            params,
        )
    )
    total = int(total_row["total"]) if total_row else 0

    rows = _rows(
        await main_db.execute(
            text(
                f"""
                SELECT
                    w.id,
                    w.workshift,
                    w.inactive,
                    w.first_last,
                    w.gracetime_early,
                    w.gracetime_late,
                    w.early_check_in,
                    w.early_check_in_time,
                    w.early_check_in_time_deduction,
                    COALESCE(d.day_count, 0) AS day_count,
                    COALESCE(b.break_count, 0) AS break_count
                FROM workshift w
                LEFT JOIN (
                    SELECT workshift_id, COUNT(*) AS day_count
                    FROM workshift_day
                    GROUP BY workshift_id
                ) d ON d.workshift_id = w.id
                LEFT JOIN (
                    SELECT workshift_id, COUNT(*) AS break_count
                    FROM workshift_break
                    GROUP BY workshift_id
                ) b ON b.workshift_id = w.id
                {where}
                ORDER BY w.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )

    for row in rows:
        row["gracetime_early"] = _format_time_hhmm(row.get("gracetime_early"))
        row["gracetime_late"] = _format_time_hhmm(row.get("gracetime_late"))
        row["early_check_in_time"] = _format_time_hhmm(row.get("early_check_in_time"))

    return success_response(
        data={"workshifts": rows, "total": total},
        message="Workshifts fetched",
    ).model_dump(mode="json")


@router.get("/{workshift_id}")
async def get_workshift(
    workshift_id: int,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "workshift:read", central_db, str(workshift_id))

    row = _row(
        await main_db.execute(
            text(
                """
                SELECT
                    id,
                    workshift,
                    first_last,
                    gracetime_early,
                    gracetime_late,
                    early_check_in,
                    early_check_in_time_deduction,
                    early_check_in_time,
                    inactive,
                    bid
                FROM workshift
                WHERE id = :workshift_id AND (park IS NULL OR park = 0)
                LIMIT 1
                """
            ),
            {"workshift_id": int(workshift_id)},
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Workshift not found")

    workshift = {
        "id": row.get("id"),
        "workshift": row.get("workshift"),
        "first_last": int(row.get("first_last") or 0),
        "gracetime_early": _format_time_hhmm(row.get("gracetime_early")),
        "gracetime_late": _format_time_hhmm(row.get("gracetime_late")),
        "early_check_in": int(row.get("early_check_in") or 0),
        "early_check_in_time_deduction": row.get("early_check_in_time_deduction") or "",
        "early_check_in_time": _format_time_hhmm(row.get("early_check_in_time")),
        "inactive": int(row.get("inactive") or 0),
        "bid": row.get("bid"),
    }

    break_rows = _rows(
        await main_db.execute(
            text(
                """
                SELECT name, start_time, end_time
                FROM workshift_break
                WHERE workshift_id = :workshift_id
                """
            ),
            {"workshift_id": int(workshift_id)},
        )
    )
    for b in break_rows:
        b["start_time"] = _format_time_hhmm(b.get("start_time"))
        b["end_time"] = _format_time_hhmm(b.get("end_time"))

    late_rows = _rows(
        await main_db.execute(
            text(
                """
                SELECT late_by, deduct, late_deductions
                FROM workshift_late
                WHERE workshift_id = :workshift_id
                """
            ),
            {"workshift_id": int(workshift_id)},
        )
    )
    for l in late_rows:
        l["late_deductions"] = int(l.get("late_deductions") or 0)

    paid_rows = _rows(
        await main_db.execute(
            text(
                """
                SELECT leaves_addition, paid_leaves_monthly
                FROM workshift_paid_leaves
                WHERE workshift_id = :workshift_id
                """
            ),
            {"workshift_id": int(workshift_id)},
        )
    )
    for p in paid_rows:
        p["leaves_addition"] = int(p.get("leaves_addition") or 0)

    non_accrual_rows = _rows(
        await main_db.execute(
            text(
                """
                SELECT leaves_to_be_given, non_accrual_leaves_monthly
                FROM workshift_non_accrual_leaves
                WHERE workshift_id = :workshift_id
                """
            ),
            {"workshift_id": int(workshift_id)},
        )
    )
    for n in non_accrual_rows:
        n["leaves_to_be_given"] = int(n.get("leaves_to_be_given") or 0)

    day_columns = await _get_workshift_day_columns(main_db)
    day_select: list[str] = []
    if "day" in day_columns:
        day_select.append("day")
    if "day_code" in day_columns:
        day_select.append("day_code")
    day_select.extend(["start_time", "end_time"])
    if "wfh" in day_columns:
        day_select.append("wfh")
    order_by = "day_code" if "day_code" in day_columns else "day"

    day_rows = _rows(
        await main_db.execute(
            text(
                f"""
                SELECT {', '.join(day_select)}
                FROM workshift_day
                WHERE workshift_id = :workshift_id
                ORDER BY {order_by} ASC
                """
            ),
            {"workshift_id": int(workshift_id)},
        )
    )

    normalized_days: list[dict] = []
    for d in day_rows:
        day_label = d.get("day")
        if day_label is not None:
            day_label = _normalize_day_label(str(day_label))
        day_code = d.get("day_code")
        if day_label is None and day_code is not None:
            day_label = _day_label_from_code(int(day_code))
        if day_code is None and day_label:
            day_code = _day_code_from_label(day_label)
        normalized_days.append(
            {
                "day": day_label or "",
                "day_code": int(day_code) if day_code is not None else None,
                "start_time": _format_time_hhmm(d.get("start_time")),
                "end_time": _format_time_hhmm(d.get("end_time")),
                "wfh": int(d.get("wfh") or 0),
            }
        )

    workshift["break"] = break_rows
    workshift["late"] = late_rows
    workshift["paid_leaves"] = paid_rows
    workshift["non_accrual_leaves"] = non_accrual_rows
    workshift["day"] = normalized_days

    return success_response(data=workshift, message="Workshift fetched").model_dump(mode="json")


@router.post("")
async def create_workshift(
    payload: WorkshiftCreateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "workshift:create", central_db)

    if not payload.day:
        raise HTTPException(status_code=422, detail="At least one workshift day is required")

    existing = _row(
        await main_db.execute(
            text("SELECT id FROM workshift WHERE workshift = :name LIMIT 1"),
            {"name": payload.workshift},
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Workshift already exists")

    params = {
        "workshift": payload.workshift,
        "first_last": int(payload.first_last),
        "gracetime_early": _normalize_time_or_default(payload.gracetime_early, "gracetime_early", "00:00:00"),
        "gracetime_late": _normalize_time_or_default(payload.gracetime_late, "gracetime_late", "00:00:00"),
        "early_check_in": int(payload.early_check_in),
        "early_check_in_time_deduction": payload.early_check_in_time_deduction or "",
        "early_check_in_time": _normalize_time_or_default(payload.early_check_in_time, "early_check_in_time", "00:00:00"),
        "inactive": int(payload.inactive),
        "bid": int(payload.bid or 27),
        "created_by": int(caller.user_id),
    }

    result = await main_db.execute(
        text(
            """
            INSERT INTO workshift (
                workshift,
                first_last,
                gracetime_early,
                gracetime_late,
                early_check_in,
                early_check_in_time_deduction,
                early_check_in_time,
                inactive,
                bid,
                created_at,
                created_by
            ) VALUES (
                :workshift,
                :first_last,
                :gracetime_early,
                :gracetime_late,
                :early_check_in,
                :early_check_in_time_deduction,
                :early_check_in_time,
                :inactive,
                :bid,
                NOW(),
                :created_by
            )
            """
        ),
        params,
    )
    workshift_id = int(getattr(result, "lastrowid", None) or 0)
    if workshift_id <= 0:
        raise HTTPException(status_code=500, detail="Failed to create workshift")

    await _replace_workshift_breaks(main_db, workshift_id, payload.breaks)
    await _replace_workshift_days(main_db, workshift_id, payload.day)
    await _replace_workshift_lates(main_db, workshift_id, payload.late)
    await _replace_workshift_paid_leaves(main_db, workshift_id, payload.paid_leaves)
    await _replace_workshift_non_accrual_leaves(main_db, workshift_id, payload.non_accrual_leaves)
    await main_db.commit()

    return success_response(
        data={"id": workshift_id},
        message="Workshift created",
    ).model_dump(mode="json")


@router.patch("/{workshift_id}")
async def update_workshift(
    workshift_id: int,
    payload: WorkshiftUpdateRequest,
    caller: CallerContext = Depends(require_any_caller),
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    await _check_prism(caller, "workshift:update", central_db, str(workshift_id))

    existing = _row(
        await main_db.execute(
            text("SELECT id, workshift FROM workshift WHERE id = :id LIMIT 1"),
            {"id": int(workshift_id)},
        )
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Workshift not found")

    if payload.workshift and payload.workshift != existing.get("workshift"):
        duplicate = _row(
            await main_db.execute(
                text(
                    """
                    SELECT id FROM workshift
                    WHERE workshift = :name AND id <> :id
                    LIMIT 1
                    """
                ),
                {"name": payload.workshift, "id": int(workshift_id)},
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Workshift already exists")

    assignments: list[str] = []
    params: dict[str, object] = {"workshift_id": int(workshift_id)}

    if payload.workshift is not None:
        assignments.append("workshift = :workshift")
        params["workshift"] = payload.workshift
    if payload.first_last is not None:
        assignments.append("first_last = :first_last")
        params["first_last"] = int(payload.first_last)
    if payload.gracetime_early is not None:
        assignments.append("gracetime_early = :gracetime_early")
        params["gracetime_early"] = _normalize_time_or_default(
            payload.gracetime_early, "gracetime_early", "00:00:00"
        )
    if payload.gracetime_late is not None:
        assignments.append("gracetime_late = :gracetime_late")
        params["gracetime_late"] = _normalize_time_or_default(
            payload.gracetime_late, "gracetime_late", "00:00:00"
        )
    if payload.early_check_in is not None:
        assignments.append("early_check_in = :early_check_in")
        params["early_check_in"] = int(payload.early_check_in)
    if payload.early_check_in_time_deduction is not None:
        assignments.append("early_check_in_time_deduction = :early_check_in_time_deduction")
        params["early_check_in_time_deduction"] = payload.early_check_in_time_deduction or ""
    if payload.early_check_in_time is not None:
        assignments.append("early_check_in_time = :early_check_in_time")
        params["early_check_in_time"] = _normalize_time_or_default(
            payload.early_check_in_time, "early_check_in_time", "00:00:00"
        )
    if payload.inactive is not None:
        assignments.append("inactive = :inactive")
        params["inactive"] = int(payload.inactive)
    if payload.bid is not None:
        assignments.append("bid = :bid")
        params["bid"] = int(payload.bid)

    if assignments:
        await main_db.execute(
            text(
                f"""
                UPDATE workshift
                SET {", ".join(assignments)}
                WHERE id = :workshift_id
                """
            ),
            params,
        )

    await _replace_workshift_breaks(main_db, workshift_id, payload.breaks)
    await _replace_workshift_days(main_db, workshift_id, payload.day)
    await _replace_workshift_lates(main_db, workshift_id, payload.late)
    await _replace_workshift_paid_leaves(main_db, workshift_id, payload.paid_leaves)
    await _replace_workshift_non_accrual_leaves(main_db, workshift_id, payload.non_accrual_leaves)
    await main_db.commit()

    return success_response(
        data={"id": int(workshift_id)},
        message="Workshift updated",
    ).model_dump(mode="json")

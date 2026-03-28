"""Repository for employee event <-> Google event mapping state."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, select, text

from core.database import engines

from ..dependencies import EmployeeEventsError

_metadata = MetaData()
_link_table = Table(
    "employee_event_google_link",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("employee_event_id", Integer, nullable=False, unique=True, index=True),
    Column("google_event_id", String(255), nullable=True),
    Column("google_calendar_id", String(255), nullable=True),
    Column("sync_status", String(32), nullable=False, default="pending_approval"),
    Column("last_error_code", String(64), nullable=True),
    Column("last_error_message", Text, nullable=True),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    ),
)


class EmployeeEventGoogleSyncRepository:
    """Persistence for google sync status."""

    @staticmethod
    def _get_main_engine():
        engine = engines.get("default")
        if engine is None:
            raise EmployeeEventsError(
                code="EMP_EVENT_DB_UNAVAILABLE",
                message="Main DB engine is not available",
                status_code=503,
            )
        return engine

    def ensure_table(self) -> None:
        engine = self._get_main_engine()
        _metadata.create_all(bind=engine, tables=[_link_table], checkfirst=True)

    def get_link(self, employee_event_id: int) -> Optional[Dict[str, Any]]:
        self.ensure_table()
        engine = self._get_main_engine()

        with engine.connect() as conn:
            row = conn.execute(
                select(_link_table).where(
                    _link_table.c.employee_event_id == int(employee_event_id)
                )
            ).mappings().first()

        if row is None:
            return None
        return dict(row)

    def upsert_pending(self, employee_event_id: int, google_calendar_id: str) -> Dict[str, Any]:
        self.ensure_table()
        engine = self._get_main_engine()
        now = datetime.utcnow()

        with engine.begin() as conn:
            row = conn.execute(
                select(_link_table).where(
                    _link_table.c.employee_event_id == int(employee_event_id)
                )
            ).mappings().first()

            if row is None:
                conn.execute(
                    _link_table.insert().values(
                        employee_event_id=int(employee_event_id),
                        google_event_id=None,
                        google_calendar_id=google_calendar_id,
                        sync_status="pending_approval",
                        last_error_code=None,
                        last_error_message=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                conn.execute(
                    _link_table.update()
                    .where(_link_table.c.employee_event_id == int(employee_event_id))
                    .values(
                        google_calendar_id=google_calendar_id,
                        sync_status=row.get("sync_status") or "pending_approval",
                        updated_at=now,
                    )
                )

        refreshed = self.get_link(employee_event_id)
        return refreshed or {}

    def mark_active(
        self,
        employee_event_id: int,
        google_event_id: str,
        google_calendar_id: str,
    ) -> None:
        self.ensure_table()
        engine = self._get_main_engine()
        now = datetime.utcnow()

        with engine.begin() as conn:
            row = conn.execute(
                select(_link_table).where(
                    _link_table.c.employee_event_id == int(employee_event_id)
                )
            ).mappings().first()

            values = {
                "google_event_id": google_event_id,
                "google_calendar_id": google_calendar_id,
                "sync_status": "active",
                "last_error_code": None,
                "last_error_message": None,
                "updated_at": now,
            }

            if row is None:
                values["employee_event_id"] = int(employee_event_id)
                values["created_at"] = now
                conn.execute(_link_table.insert().values(**values))
            else:
                conn.execute(
                    _link_table.update()
                    .where(_link_table.c.employee_event_id == int(employee_event_id))
                    .values(**values)
                )

    def mark_error(
        self,
        employee_event_id: int,
        sync_status: str,
        error_code: str,
        error_message: str,
    ) -> None:
        self.ensure_table()
        engine = self._get_main_engine()
        now = datetime.utcnow()

        with engine.begin() as conn:
            row = conn.execute(
                select(_link_table).where(
                    _link_table.c.employee_event_id == int(employee_event_id)
                )
            ).mappings().first()

            values = {
                "sync_status": sync_status,
                "last_error_code": error_code,
                "last_error_message": error_message,
                "updated_at": now,
            }

            if row is None:
                values["employee_event_id"] = int(employee_event_id)
                values["created_at"] = now
                conn.execute(_link_table.insert().values(**values))
            else:
                conn.execute(
                    _link_table.update()
                    .where(_link_table.c.employee_event_id == int(employee_event_id))
                    .values(**values)
                )

    def mark_deleted(self, employee_event_id: int) -> None:
        self.ensure_table()
        engine = self._get_main_engine()
        now = datetime.utcnow()
        with engine.begin() as conn:
            row = conn.execute(
                select(_link_table).where(
                    _link_table.c.employee_event_id == int(employee_event_id)
                )
            ).mappings().first()
            if row is None:
                conn.execute(
                    _link_table.insert().values(
                        employee_event_id=int(employee_event_id),
                        sync_status="deleted",
                        last_error_code=None,
                        last_error_message=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                conn.execute(
                    _link_table.update()
                    .where(_link_table.c.employee_event_id == int(employee_event_id))
                    .values(
                        sync_status="deleted",
                        last_error_code=None,
                        last_error_message=None,
                        updated_at=now,
                    )
                )

    def get_links_by_event_ids(self, employee_event_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        self.ensure_table()
        if not employee_event_ids:
            return {}

        engine = self._get_main_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                select(_link_table).where(
                    _link_table.c.employee_event_id.in_([int(eid) for eid in employee_event_ids])
                )
            ).mappings().all()

        mapped: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            mapped[int(data["employee_event_id"])] = data
        return mapped

"""Workforce schema constants."""

from __future__ import annotations

from typing import Final

FUTURE_SCOPE_EMPLOYEE: Final[list[str]] = [
    "employee_profile_edit",
    "employee_document_management",
    "employee_role_assignment",
    "employee_lifecycle_actions",
]

FUTURE_SCOPE_ATTENDANCE: Final[list[str]] = [
    "attendance_log_ingestion",
    "attendance_regularization",
    "leave_approval_workflows",
    "shift_planning_and_rosters",
]

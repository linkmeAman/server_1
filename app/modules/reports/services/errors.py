"""Custom API exceptions for report admin flows."""

from __future__ import annotations

from fastapi import HTTPException

from app.modules.reports.schemas.models import ReportFieldError


class ReportApiException(HTTPException):
    """HTTPException with a stable error code and response data payload."""

    def __init__(
        self,
        status_code: int,
        *,
        error_code: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.error_code = error_code
        self.response_data = data


class ReportValidationException(ReportApiException):
    """422 validation error with field-level details."""

    def __init__(self, field_errors: list[ReportFieldError]) -> None:
        self.field_errors = field_errors
        super().__init__(
            422,
            error_code="ReportValidationError",
            message="Report definition validation failed",
            data={"field_errors": [item.model_dump(mode="json") for item in field_errors]},
        )

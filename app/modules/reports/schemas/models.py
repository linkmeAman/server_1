"""Pydantic models for the generic report platform."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ReportKind = Literal["table", "route"]
ReportStatus = Literal["draft", "published", "archived"]
ReportAdminStatus = Literal["draft", "published", "archived", "all"]
FilterOperator = Literal[
    "eq",
    "ne",
    "contains",
    "starts_with",
    "in",
    "between",
    "gte",
    "lte",
    "gt",
    "lt",
    "is_null",
    "not_null",
]
SortDirection = Literal["asc", "desc"]


def _format_display_label(value: str) -> str:
    cleaned = " ".join(part for part in value.replace("-", "_").split("_") if part)
    if not cleaned:
        return ""
    return " ".join(chunk[:1].upper() + chunk[1:] for chunk in cleaned.split())


def _resolve_display_label(
    *,
    display_label: str | None,
    label: str | None,
    key: str | None,
) -> str:
    for candidate in (display_label, label, _format_display_label(key or "")):
        normalized = (candidate or "").strip()
        if normalized:
            return normalized
    return ""


class ReportColumn(BaseModel):
    key: str = Field(..., min_length=1, max_length=128)
    label: str = Field(..., min_length=1, max_length=191)
    display_label: str | None = Field(default=None, max_length=191)
    type: str = Field(default="text", max_length=32)
    visible: bool = True
    sortable: bool = True
    searchable: bool = False
    exportable: bool = False
    width: int | None = Field(default=None, ge=40, le=800)

    @model_validator(mode="after")
    def _sync_display_label(self) -> "ReportColumn":
        display_label = _resolve_display_label(
            display_label=self.display_label,
            label=self.label,
            key=self.key,
        )
        self.display_label = display_label
        self.label = display_label
        return self


class ReportFilter(BaseModel):
    key: str = Field(..., min_length=1, max_length=128)
    label: str = Field(..., min_length=1, max_length=191)
    display_label: str | None = Field(default=None, max_length=191)
    column: str = Field(..., min_length=1, max_length=128)
    operators: list[FilterOperator] = Field(default_factory=lambda: ["eq"])
    type: str = Field(default="text", max_length=32)
    options: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_display_label(self) -> "ReportFilter":
        display_label = _resolve_display_label(
            display_label=self.display_label,
            label=self.label,
            key=self.key,
        )
        self.display_label = display_label
        self.label = display_label
        return self


class ReportSort(BaseModel):
    column: str = Field(..., min_length=1, max_length=128)
    direction: SortDirection = "asc"


class ReportAction(BaseModel):
    key: str = Field(..., min_length=1, max_length=128)
    label: str = Field(..., min_length=1, max_length=191)
    icon: str | None = Field(default=None, max_length=64)
    permission: str | None = Field(default=None, max_length=191)
    route_template: str | None = Field(default=None, max_length=512)
    predicate: dict[str, Any] | None = None


class ReportSource(BaseModel):
    type: ReportKind
    database: str | None = Field(default=None, max_length=128)
    table: str | None = Field(default=None, max_length=191)
    route_path: str | None = Field(default=None, max_length=512)
    id_column: str | None = Field(default=None, max_length=128)
    date_column: str | None = Field(default=None, max_length=128)
    branch_column: str | None = Field(default=None, max_length=128)


class ReportDateRange(BaseModel):
    enabled: bool = False
    default_days: int = Field(default=30, ge=1, le=366)
    column: str | None = Field(default=None, max_length=128)


class ReportBranchScope(BaseModel):
    mode: Literal["all", "token_branch"] = "all"
    column: str | None = Field(default=None, max_length=128)


class ReportDefinitionBase(BaseModel):
    slug: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=191)
    description: str | None = None
    category: str = Field(default="Reports", max_length=128)
    kind: ReportKind = "table"
    legacy_report_id: int | None = None
    prism_resource_code: str = Field(..., min_length=1, max_length=191)
    legacy_view_action: str | None = Field(default=None, max_length=191)
    source: ReportSource
    columns: list[ReportColumn] = Field(default_factory=list)
    filters: list[ReportFilter] = Field(default_factory=list)
    default_sort: list[ReportSort] = Field(default_factory=list)
    search_columns: list[str] = Field(default_factory=list)
    date_range: ReportDateRange = Field(default_factory=ReportDateRange)
    branch_scope: ReportBranchScope = Field(default_factory=ReportBranchScope)
    actions: list[ReportAction] = Field(default_factory=list)
    route_path: str | None = Field(default=None, max_length=512)


class ReportDefinition(ReportDefinitionBase):
    status: ReportStatus = "published"
    version: int = Field(default=1, ge=1)
    source_label: str = Field(default="certified", max_length=64)


class ReportDraftUpsertRequest(ReportDefinitionBase):
    source_label: str = Field(default="custom-admin", max_length=64)


class ReportCatalogItem(BaseModel):
    slug: str
    name: str
    description: str | None = None
    category: str
    kind: ReportKind
    status: ReportStatus
    route_path: str
    prism_resource_code: str
    legacy_report_id: int | None = None
    source_label: str
    available: bool = True
    unavailable_reason: str | None = None


class ReportQueryFilter(BaseModel):
    column: str = Field(..., min_length=1, max_length=128)
    operator: FilterOperator
    value: Any = None

    @field_validator("operator", mode="before")
    @classmethod
    def _normalize_operator(cls, value: Any) -> Any:
        aliases = {
            "is_empty": "is_null",
            "has_any_value": "not_null",
        }
        if isinstance(value, str):
            return aliases.get(value.strip().lower(), value)
        return value


class ReportQueryDateRange(BaseModel):
    start: str | None = None
    end: str | None = None


class ReportQuerySort(BaseModel):
    column: str = Field(..., min_length=1, max_length=128)
    direction: SortDirection = "asc"


class ReportQueryRequest(BaseModel):
    date_range: ReportQueryDateRange | None = None
    filters: list[ReportQueryFilter] = Field(default_factory=list)
    search: str | None = Field(default=None, max_length=200)
    sort: list[ReportQuerySort] = Field(default_factory=list)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class ReportQueryResponse(BaseModel):
    slug: str
    columns: list[ReportColumn]
    rows: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    sort: list[ReportQuerySort]
    actions: list[ReportAction] = Field(default_factory=list)


class ReportFieldError(BaseModel):
    path: str = Field(..., min_length=1, max_length=256)
    code: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1, max_length=512)


class ReportValidationErrorResponse(BaseModel):
    field_errors: list[ReportFieldError] = Field(default_factory=list)


class ReportDraftResponse(BaseModel):
    report: ReportDefinition
    validation_issues: list[ReportFieldError] = Field(default_factory=list)


class ReportVersionSummary(BaseModel):
    id: int | str | None = None
    slug: str | None = None
    version: int = Field(..., ge=1)
    status: ReportStatus = "draft"
    created_at: str | None = None
    updated_at: str | None = None
    modified_at: str | None = None
    created_by_user_id: int | str | None = None
    created_by_name: str | None = None
    modified_by_user_id: int | str | None = None
    modified_by_name: str | None = None
    owner_name: str | None = None
    is_active: bool = False
    is_published: bool = False
    report: ReportDefinition | None = None


class ReportVersionHistoryResponse(BaseModel):
    versions: list[ReportVersionSummary] = Field(default_factory=list)


class ReportAdminListResponse(BaseModel):
    reports: list[ReportDefinition] = Field(default_factory=list)


class ReportImportDraftResponse(BaseModel):
    report: ReportDefinition
    warnings: list[str] = Field(default_factory=list)
    imported_legacy_report_id: int


class ReportDraftListResponse(BaseModel):
    drafts: list[ReportDefinition] = Field(default_factory=list)


class LegacyReportCandidate(BaseModel):
    legacy_report_id: int
    name: str
    description: str | None = None
    category: str = "Legacy Reports"
    source_table: str | None = None
    dynamic_report: bool = False
    already_migrated: bool = False
    existing_report_slug: str | None = None
    existing_report_status: ReportStatus | None = None
    available_for_import: bool = True
    unavailable_reason: str | None = None


class LegacyImportIssue(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1, max_length=512)
    field_path: str | None = Field(default=None, max_length=256)
    technical_detail: str | None = Field(default=None, max_length=1000)


class LegacyImportItemResult(BaseModel):
    legacy_report_id: int
    name: str
    status: Literal["imported", "imported_with_issues", "failed"]
    report: ReportDefinition | None = None
    issues: list[LegacyImportIssue] = Field(default_factory=list)


class LegacyImportBatchRequest(BaseModel):
    report_ids: list[int] = Field(default_factory=list, min_length=1)


class LegacyImportBatchResponse(BaseModel):
    results: list[LegacyImportItemResult] = Field(default_factory=list)
    total_requested: int = Field(default=0, ge=0)
    imported_count: int = Field(default=0, ge=0)
    imported_with_issues_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)


ReportAdminSaveRequest = ReportDraftUpsertRequest

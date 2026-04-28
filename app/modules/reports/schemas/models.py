"""Pydantic models for the generic report platform."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ReportKind = Literal["table", "route"]
ReportStatus = Literal["draft", "published", "archived"]
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


class ReportColumn(BaseModel):
    key: str = Field(..., min_length=1, max_length=128)
    label: str = Field(..., min_length=1, max_length=191)
    type: str = Field(default="text", max_length=32)
    visible: bool = True
    sortable: bool = True
    searchable: bool = False
    exportable: bool = False
    width: int | None = Field(default=None, ge=40, le=800)


class ReportFilter(BaseModel):
    key: str = Field(..., min_length=1, max_length=128)
    label: str = Field(..., min_length=1, max_length=191)
    column: str = Field(..., min_length=1, max_length=128)
    operators: list[FilterOperator] = Field(default_factory=lambda: ["eq"])
    type: str = Field(default="text", max_length=32)
    options: list[dict[str, Any]] = Field(default_factory=list)


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


class ReportDefinition(BaseModel):
    slug: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=191)
    description: str | None = None
    category: str = Field(default="Reports", max_length=128)
    status: ReportStatus = "published"
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
    version: int = Field(default=1, ge=1)
    source_label: str = Field(default="certified", max_length=64)


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


class ReportQueryFilter(BaseModel):
    column: str = Field(..., min_length=1, max_length=128)
    operator: FilterOperator
    value: Any = None


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


class ReportAdminSaveRequest(BaseModel):
    slug: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=191)
    description: str | None = None
    category: str = Field(default="Reports", max_length=128)
    definition: dict[str, Any]


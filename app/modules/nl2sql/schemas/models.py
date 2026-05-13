"""Request and response models for the NL2SQL integration module."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictStr, TypeAdapter, field_validator


class Nl2SqlRequest(BaseModel):
    """App-facing request body forwarded to the external NL2SQL service."""

    query: StrictStr
    top_k: int | None = Field(default=None, ge=0, strict=True)
    request_id: StrictStr | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class Nl2SqlWarning(BaseModel):
    """Warning returned by the upstream NL2SQL service."""

    code: str
    message: str

    model_config = ConfigDict(extra="allow")


class Nl2SqlOkBase(BaseModel):
    """Common base for successful NL2SQL responses."""

    status: Literal["ok"]
    warnings: list[Nl2SqlWarning] = Field(default_factory=list)
    react_trace: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class Nl2SqlClarificationBase(BaseModel):
    """Common base for clarification responses."""

    status: Literal["clarification_needed"]
    question: str
    suggestions: list[str] = Field(default_factory=list)
    original_query: str
    failure_reason: str
    react_trace: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class Nl2SqlRejectedBase(BaseModel):
    """Common base for transport or execution failures."""

    status: Literal["rejected"]
    warnings: list[Nl2SqlWarning] = Field(default_factory=list)
    attempt_count: int | None = None
    react_trace: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class Nl2SqlAskSuccess(Nl2SqlOkBase):
    """Validated upstream `/ask` success response."""

    answer: str
    sql: str
    row_count: int
    columns: list[str] = Field(default_factory=list)
    tables_used: list[str] = Field(default_factory=list)
    matched_groups: list[str] = Field(default_factory=list)
    attempt_count: int | None = None


class Nl2SqlAskClarification(Nl2SqlClarificationBase):
    """Validated upstream `/ask` clarification response."""


class Nl2SqlAskRejected(Nl2SqlRejectedBase):
    """Validated upstream `/ask` rejected response."""

    answer: str | None = None
    sql: str | None = None


class Nl2SqlGenerateSqlSuccess(Nl2SqlOkBase):
    """Validated upstream `/generate-sql` success response."""

    sql: str
    tables_used: list[str] = Field(default_factory=list)
    matched_groups: list[str] = Field(default_factory=list)
    attempt_count: int | None = None


class Nl2SqlGenerateSqlClarification(Nl2SqlClarificationBase):
    """Validated upstream `/generate-sql` clarification response."""


class Nl2SqlGenerateSqlRejected(Nl2SqlRejectedBase):
    """Validated upstream `/generate-sql` rejected response."""

    sql: str | None = None


Nl2SqlAskResponse = Annotated[
    Union[
        Nl2SqlAskSuccess,
        Nl2SqlAskClarification,
        Nl2SqlAskRejected,
    ],
    Field(discriminator="status"),
]

Nl2SqlGenerateSqlResponse = Annotated[
    Union[
        Nl2SqlGenerateSqlSuccess,
        Nl2SqlGenerateSqlClarification,
        Nl2SqlGenerateSqlRejected,
    ],
    Field(discriminator="status"),
]

ASK_RESPONSE_ADAPTER = TypeAdapter(Nl2SqlAskResponse)
GENERATE_SQL_RESPONSE_ADAPTER = TypeAdapter(Nl2SqlGenerateSqlResponse)


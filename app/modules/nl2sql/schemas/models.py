"""Request and response models for the NL2SQL integration module."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, TypeAdapter, field_validator


class Nl2SqlRequest(BaseModel):
    """App-facing request body forwarded to the external NL2SQL service."""

    query: StrictStr
    top_k: StrictInt | None = Field(default=None, ge=0)
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
    code: str
    message: str

    model_config = ConfigDict(extra="allow")


class Nl2SqlOkBase(BaseModel):
    status: Literal["ok"]
    warnings: list[Nl2SqlWarning] = Field(default_factory=list)
    cache_hit: bool = False
    cache_source: str = "none"
    react_trace: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class Nl2SqlClarificationBase(BaseModel):
    status: Literal["clarification_needed"]
    question: str
    suggestions: list[str] = Field(default_factory=list)
    original_query: str
    failure_reason: str
    cache_hit: bool = False
    cache_source: str = "none"
    react_trace: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class Nl2SqlRejectedBase(BaseModel):
    status: Literal["rejected"]
    warnings: list[Nl2SqlWarning] = Field(default_factory=list)
    attempt_count: int | None = None
    cache_hit: bool = False
    cache_source: str = "none"
    react_trace: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class Nl2SqlAskSuccess(Nl2SqlOkBase):
    answer: str
    sql: str
    row_count: int
    columns: list[str] = Field(default_factory=list)
    tables_used: list[str] = Field(default_factory=list)
    matched_groups: list[str] = Field(default_factory=list)
    attempt_count: int | None = None


class Nl2SqlAskClarification(Nl2SqlClarificationBase):
    pass


class Nl2SqlAskRejected(Nl2SqlRejectedBase):
    answer: str | None = None
    sql: str | None = None


class Nl2SqlGenerateSqlSuccess(Nl2SqlOkBase):
    sql: str
    tables_used: list[str] = Field(default_factory=list)
    matched_groups: list[str] = Field(default_factory=list)
    attempt_count: int | None = None


class Nl2SqlGenerateSqlClarification(Nl2SqlClarificationBase):
    pass


class Nl2SqlGenerateSqlRejected(Nl2SqlRejectedBase):
    sql: str | None = None


Nl2SqlAskResponse = Annotated[
    Union[Nl2SqlAskSuccess, Nl2SqlAskClarification, Nl2SqlAskRejected],
    Field(discriminator="status"),
]

Nl2SqlGenerateSqlResponse = Annotated[
    Union[Nl2SqlGenerateSqlSuccess, Nl2SqlGenerateSqlClarification, Nl2SqlGenerateSqlRejected],
    Field(discriminator="status"),
]


class Nl2SqlTeachRequest(BaseModel):
    instruction_type: StrictStr
    content: StrictStr
    tables_affected: list[StrictStr] = Field(default_factory=list)
    source_query: StrictStr | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("instruction_type", "content")
    @classmethod
    def strip_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be blank")
        return normalized

    @field_validator("source_query")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class Nl2SqlConfirmTeachRequest(BaseModel):
    confirmation_token: StrictStr
    action: Literal["confirm", "reject", "replace"]

    model_config = ConfigDict(extra="ignore")

    @field_validator("confirmation_token")
    @classmethod
    def strip_token(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("confirmation_token must not be blank")
        return normalized


class Nl2SqlSimilarInstruction(BaseModel):
    id: int
    instruction_type: str
    content: str
    confidence_score: float
    is_verified: bool
    use_count: int

    model_config = ConfigDict(extra="allow")


class Nl2SqlTeachResponse(BaseModel):
    learning_status: str
    message: str
    instruction_id: int | None = None
    similar_instructions: list[Nl2SqlSimilarInstruction] = Field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_token: str | None = None

    model_config = ConfigDict(extra="allow")


class Nl2SqlInstruction(BaseModel):
    id: int
    instruction_type: str
    content: str
    tables_affected: list[str] = Field(default_factory=list)
    confidence_score: float
    is_verified: bool
    is_active: bool
    use_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used_at: str | None = None
    created_at: str | None = None

    model_config = ConfigDict(extra="allow")


class Nl2SqlInstructionsQuery(BaseModel):
    instruction_type: StrictStr | None = None
    active_only: bool = True

    model_config = ConfigDict(extra="ignore")

    @field_validator("instruction_type")
    @classmethod
    def normalize_instruction_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class Nl2SqlIngestGroupsRequest(BaseModel):
    group_names: list[StrictStr] | None = None

    model_config = ConfigDict(extra="ignore")


class Nl2SqlIngestKnowledgeRequest(BaseModel):
    include_column_catalog: StrictBool = True
    include_sql_examples: StrictBool = True
    include_relations: StrictBool = True
    include_graph: StrictBool = True
    include_view_registry: StrictBool = True
    include_onboarding_rules: StrictBool = True
    column_limit: StrictInt | None = None
    sql_example_limit: StrictInt | None = None
    relation_limit: StrictInt | None = None
    graph_limit: StrictInt | None = None
    view_registry_limit: StrictInt | None = None

    model_config = ConfigDict(extra="ignore")


class Nl2SqlIngestEmbeddedRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Nl2SqlEnrichmentSummary(BaseModel):
    groups_with_columns: int
    groups_without_columns: int
    groups_with_aliases: int
    groups_with_examples: int

    model_config = ConfigDict(extra="allow")


class Nl2SqlGroupIngestFailure(BaseModel):
    group_name: str
    reason: str

    model_config = ConfigDict(extra="allow")


class Nl2SqlIngestResponse(BaseModel):
    inserted: int
    updated: int = 0
    skipped: int = 0
    source: str

    model_config = ConfigDict(extra="allow")


class Nl2SqlIngestGroupsResponse(Nl2SqlIngestResponse):
    enrichment_summary: Nl2SqlEnrichmentSummary | None = None
    failed_groups: list[Nl2SqlGroupIngestFailure] = Field(default_factory=list)
    failure_count: int = 0


class Nl2SqlEmbeddedIngestResponse(Nl2SqlIngestResponse):
    embedded: int = 0


ASK_RESPONSE_ADAPTER = TypeAdapter(Nl2SqlAskResponse)
GENERATE_SQL_RESPONSE_ADAPTER = TypeAdapter(Nl2SqlGenerateSqlResponse)
TEACH_RESPONSE_ADAPTER = TypeAdapter(Nl2SqlTeachResponse)
INSTRUCTIONS_RESPONSE_ADAPTER = TypeAdapter(list[Nl2SqlInstruction])
INGEST_GROUPS_RESPONSE_ADAPTER = TypeAdapter(Nl2SqlIngestGroupsResponse)
INGEST_RESPONSE_ADAPTER = TypeAdapter(Nl2SqlIngestResponse)
EMBEDDED_INGEST_RESPONSE_ADAPTER = TypeAdapter(Nl2SqlEmbeddedIngestResponse)

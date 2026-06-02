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


class Nl2SqlHealthLlmQuery(BaseModel):
    role: Literal["sql", "reasoning", "query_rewrite", "answer", "default"] = "sql"

    model_config = ConfigDict(extra="ignore")


class Nl2SqlTelemetryRecentQuery(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    endpoint: StrictStr | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("endpoint")
    @classmethod
    def normalize_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class Nl2SqlTelemetrySummaryQuery(BaseModel):
    endpoint: StrictStr | None = None
    since_minutes: int = Field(default=1440, ge=1)

    model_config = ConfigDict(extra="ignore")

    @field_validator("endpoint")
    @classmethod
    def normalize_summary_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class Nl2SqlBenchmarkCaseCreateRequest(BaseModel):
    query: StrictStr
    gold_sql: StrictStr | None = None
    expected_status: StrictStr
    slices: list[StrictStr] = Field(default_factory=list)
    error_label: StrictStr | None = None
    source: StrictStr | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")

    @field_validator("query", "expected_status")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be blank")
        return normalized

    @field_validator("gold_sql", "error_label", "source")
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class Nl2SqlBenchmarkCasesQuery(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    active_only: bool = True

    model_config = ConfigDict(extra="ignore")


class Nl2SqlGovernanceValidateRequest(BaseModel):
    sql: StrictStr
    query: StrictStr
    tables_in_scope: list[StrictStr] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @field_validator("sql", "query")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be blank")
        return normalized


class Nl2SqlPatternFeedbackRequest(BaseModel):
    pattern_id: StrictInt
    helpful: StrictBool

    model_config = ConfigDict(extra="ignore")


class Nl2SqlTeachPendingQuery(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    include_expired: bool = False

    model_config = ConfigDict(extra="ignore")


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


class Nl2SqlModelRoutingPatchRequest(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_fallback_provider: str | None = None
    llm_fallback_model: str | None = None
    llm_fallback_api_key: str | None = None
    llm_fallback_base_url: str | None = None

    sql_model_provider: str | None = None
    sql_model: str | None = None
    sql_model_api_key: str | None = None
    sql_model_base_url: str | None = None
    sql_fallback_provider: str | None = None
    sql_fallback_model: str | None = None
    sql_fallback_api_key: str | None = None
    sql_fallback_base_url: str | None = None

    reasoning_model_provider: str | None = None
    reasoning_model: str | None = None
    reasoning_model_api_key: str | None = None
    reasoning_model_base_url: str | None = None
    reasoning_fallback_provider: str | None = None
    reasoning_fallback_model: str | None = None
    reasoning_fallback_api_key: str | None = None
    reasoning_fallback_base_url: str | None = None

    query_rewrite_model_provider: str | None = None
    query_rewrite_model: str | None = None
    query_rewrite_model_api_key: str | None = None
    query_rewrite_model_base_url: str | None = None
    query_rewrite_fallback_provider: str | None = None
    query_rewrite_fallback_model: str | None = None
    query_rewrite_fallback_api_key: str | None = None
    query_rewrite_fallback_base_url: str | None = None

    answer_model_provider: str | None = None
    answer_model: str | None = None
    answer_model_api_key: str | None = None
    answer_model_base_url: str | None = None
    answer_fallback_provider: str | None = None
    answer_fallback_model: str | None = None
    answer_fallback_api_key: str | None = None
    answer_fallback_base_url: str | None = None

    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None
    embedding_api_url: str | None = None

    startup_enforcement_mode: str | None = None

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


class Nl2SqlFailureLogEntry(BaseModel):
    id: int
    request_id: str
    endpoint: str
    query_text: str
    warning_codes: list[str] = Field(default_factory=list)
    error_source: str | None = None
    sql_preview: str | None = None
    tables_attempted: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    suggest_teach: dict | None = None
    created_at: str

    model_config = ConfigDict(extra="allow")


FAILURE_LOG_RESPONSE_ADAPTER = TypeAdapter(list[Nl2SqlFailureLogEntry])


class Nl2SqlTraceEvent(BaseModel):
    request_id: str
    seq: int
    layer: str
    stage: str
    status: str
    message: str
    duration_ms: int | None = None
    warning_codes: list[str] = Field(default_factory=list)
    error_source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str

    model_config = ConfigDict(extra="allow")


TRACE_EVENTS_RESPONSE_ADAPTER = TypeAdapter(list[Nl2SqlTraceEvent])
GENERIC_OBJECT_RESPONSE_ADAPTER = TypeAdapter(dict[str, Any])
GENERIC_LIST_RESPONSE_ADAPTER = TypeAdapter(list[dict[str, Any]])

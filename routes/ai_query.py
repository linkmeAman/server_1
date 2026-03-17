"""AI-assisted SQL query builder endpoint for the read-only DB explorer."""

from __future__ import annotations

import os
from typing import Any, List

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field as PydanticField

from db.query_validator import QueryValidationError, apply_row_limit, validate_query

router = APIRouter(prefix="/api", tags=["db-explorer"])


class SchemaColumn(BaseModel):
    Field: str = PydanticField(..., min_length=1)
    Type: str = PydanticField(default="")


class AIQueryRequest(BaseModel):
    prompt: str = PydanticField(..., min_length=1)
    tableName: str = PydanticField(..., min_length=1)
    schema: List[SchemaColumn] = PydanticField(default_factory=list)


def _schema_text(schema: List[SchemaColumn]) -> str:
    if not schema:
        return "(schema unavailable)"
    return "\n".join(f"- {column.Field}: {column.Type}" for column in schema)


@router.post("/ai-query")
async def generate_ai_query(payload: AIQueryRequest) -> dict[str, Any]:
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("ChatGPT_API_KEY") or "").strip()
    model = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()

    if not api_key:
        raise HTTPException(status_code=500, detail="Missing OPENAI_API_KEY/ChatGPT_API_KEY")

    system_prompt = (
        "You generate SQL for a strict read-only MySQL explorer. "
        "Return exactly one SQL SELECT statement and no explanation. "
        "Do not include markdown code fences. "
        "Never use INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, or REVOKE. "
        f"Target table: {payload.tableName}\n"
        f"Table schema:\n{_schema_text(payload.schema)}"
    )

    request_body = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload.prompt},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI request failed: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:500] if response.text else "AI provider returned an error"
        raise HTTPException(status_code=502, detail=detail)

    data = response.json()
    generated_query = str(
        data.get("choices", [{}])[0].get("message", {}).get("content", "")
    ).strip()

    if generated_query.startswith("```"):
        generated_query = generated_query.strip("`")
        if generated_query.lower().startswith("sql"):
            generated_query = generated_query[3:].strip()

    try:
        safe_query = validate_query(generated_query)
        safe_query = apply_row_limit(safe_query)
    except QueryValidationError as exc:
        raise HTTPException(status_code=502, detail=f"AI generated invalid SQL: {exc}") from exc

    return {"query": safe_query}

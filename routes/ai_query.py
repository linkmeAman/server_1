"""AI-assisted SQL query builder endpoint for the read-only DB explorer."""

from __future__ import annotations

import asyncio
import os
from typing import Any, List, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field as PydanticField

from controllers import llm as local_llm
from db.query_validator import QueryValidationError, apply_row_limit, validate_query

router = APIRouter(prefix="/api", tags=["db-explorer"])


class SchemaColumn(BaseModel):
    Field: str = PydanticField(..., min_length=1)
    Type: str = PydanticField(default="")


class AIQueryRequest(BaseModel):
    prompt: str = PydanticField(..., min_length=1)
    tableName: str = PydanticField(..., min_length=1)
    schema: List[SchemaColumn] = PydanticField(default_factory=list)
    provider: Literal["chatgpt", "local"] = "chatgpt"


def _schema_text(schema: List[SchemaColumn]) -> str:
    if not schema:
        return "(schema unavailable)"
    return "\n".join(f"- {column.Field}: {column.Type}" for column in schema)


async def _generate_openai_query(payload: AIQueryRequest) -> str:
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
    return str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


async def _generate_local_llm_query(payload: AIQueryRequest) -> str:
    llm_api_url = (os.getenv("LLM_API_URL") or "").strip().rstrip("/")
    llm_api_key = (os.getenv("LLM_API_KEY") or "").strip()
    model = (os.getenv("LLM_DEFAULT_MODEL") or "phi-3").strip()

    system_prompt = (
        "You generate SQL for a strict read-only MySQL explorer. "
        "Return exactly one SQL SELECT statement and no explanation. "
        "Do not include markdown code fences. "
        "Never use INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, or REVOKE. "
        f"Target table: {payload.tableName}\n"
        f"Table schema:\n{_schema_text(payload.schema)}"
    )

    if llm_api_url:
        headers = {"Content-Type": "application/json"}
        if llm_api_key:
            headers["X-API-Key"] = llm_api_key

        request_body = {
            "message": payload.prompt,
            "model": model,
            "temperature": 0.1,
            "max_tokens": 512,
            "system_prompt": system_prompt,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{llm_api_url}/llm/chat",
                    headers=headers,
                    json=request_body,
                )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Local LLM request failed: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text[:500] if response.text else "Local LLM provider returned an error"
            raise HTTPException(status_code=502, detail=detail)

        data = response.json()
        response_text = str(data.get("response") or "").strip()
        if not response_text:
            response_text = str(data.get("data", {}).get("response") or "").strip()

        if not response_text:
            error_text = str(data.get("error") or data.get("data", {}).get("error") or "Local LLM returned empty response")
            raise HTTPException(status_code=502, detail=error_text)

        return response_text

    result = await asyncio.to_thread(
        local_llm.chat,
        message=payload.prompt,
        model=model,
        temperature=0.1,
        max_tokens=512,
        system_prompt=system_prompt,
    )

    response_text = str(result.get("response") or "").strip()
    if not response_text:
        error_text = str(result.get("error") or "Local LLM returned empty response")
        raise HTTPException(status_code=502, detail=error_text)
    return response_text


@router.post("/ai-query")
async def generate_ai_query(payload: AIQueryRequest) -> dict[str, Any]:
    if payload.provider == "local":
        generated_query = await _generate_local_llm_query(payload)
    else:
        generated_query = await _generate_openai_query(payload)

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

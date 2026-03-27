"""Standard APIRouter endpoints mapped from legacy llm controller."""

from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.modules.llm import service
from app.shared.response_normalization import normalize_result

router = APIRouter(prefix="/api/llm", tags=["llm-standard"])


class ChatRequest(BaseModel):
    message: Optional[str] = None
    messages: Optional[List[Dict[str, str]]] = None
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2048
    system_prompt: Optional[str] = None


class CompleteRequest(BaseModel):
    prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2048


class ConversationRequest(BaseModel):
    messages: Optional[List[Dict[str, str]]] = None
    new_message: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2048
    system_prompt: str = "You are a helpful AI assistant."


@router.get("/health")
async def health():
    return normalize_result(service.health())


@router.get("/models")
async def models():
    return normalize_result(service.models())


@router.post("/chat")
async def chat(payload: ChatRequest):
    return normalize_result(
        service.chat(
            message=payload.message,
            messages=payload.messages,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            system_prompt=payload.system_prompt,
        )
    )


@router.post("/complete")
async def complete(payload: CompleteRequest):
    return normalize_result(
        service.complete(
            prompt=payload.prompt,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
    )


@router.post("/conversation")
async def conversation(payload: ConversationRequest):
    return normalize_result(
        service.conversation(
            messages=payload.messages,
            new_message=payload.new_message,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            system_prompt=payload.system_prompt,
        )
    )


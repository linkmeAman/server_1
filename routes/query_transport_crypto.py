"""Optional encrypted transport helpers for DB Explorer query endpoints."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

ENCRYPTION_HEADER = "x-dbx-encrypted"
_ENCRYPTION_SECRET_ENV = "DB_EXPLORER_QUERY_TUNNEL_SECRET"
_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000


class _EncryptedEnvelope(BaseModel):
    v: int
    ts: int
    iv: str
    ct: str
    tag: str


def _current_ms() -> int:
    return int(time.time() * 1000)


def _is_encrypted_request(request: Request) -> bool:
    return request.headers.get(ENCRYPTION_HEADER, "").strip() == "1"


def _get_secret() -> str:
    return (os.getenv(_ENCRYPTION_SECRET_ENV) or "").strip()


def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _b64url_decode(value: str) -> bytes:
    padded = value + ("=" * ((4 - len(value) % 4) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


async def parse_request_payload(request: Request) -> dict[str, Any]:
    """Parse plain or encrypted JSON payload from DB Explorer query endpoints."""

    try:
        raw_payload = await request.json()
    except Exception as exc:  # pragma: no cover - defensive for malformed JSON
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(raw_payload, dict):
        raise HTTPException(status_code=400, detail="JSON payload must be an object")

    if not _is_encrypted_request(request):
        return raw_payload

    secret = _get_secret()
    if not secret:
        raise HTTPException(
            status_code=500,
            detail=f"{_ENCRYPTION_SECRET_ENV} is required for encrypted query transport",
        )

    try:
        envelope = _EncryptedEnvelope.model_validate(raw_payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid encrypted payload envelope") from exc

    if envelope.v != 1:
        raise HTTPException(status_code=400, detail="Unsupported encrypted payload version")

    now = _current_ms()
    if abs(now - int(envelope.ts)) > _MAX_CLOCK_SKEW_MS:
        raise HTTPException(status_code=401, detail="Encrypted payload timestamp is invalid or expired")

    try:
        aes = AESGCM(_derive_key(secret))
        iv = _b64url_decode(envelope.iv)
        ciphertext = _b64url_decode(envelope.ct)
        tag = _b64url_decode(envelope.tag)
        plaintext = aes.decrypt(iv, ciphertext + tag, None)
        decoded = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid encrypted payload") from exc

    if not isinstance(decoded, dict):
        raise HTTPException(status_code=400, detail="Decrypted payload must be a JSON object")

    return decoded


def build_response(data: dict[str, Any], request: Request, status_code: int = 200) -> dict[str, Any] | JSONResponse:
    """Return plain JSON by default, encrypted envelope when request asks for it."""

    if not _is_encrypted_request(request):
        return data

    secret = _get_secret()
    if not secret:
        raise HTTPException(
            status_code=500,
            detail=f"{_ENCRYPTION_SECRET_ENV} is required for encrypted query transport",
        )

    plaintext = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    iv = os.urandom(12)
    encrypted = AESGCM(_derive_key(secret)).encrypt(iv, plaintext, None)
    ciphertext, tag = encrypted[:-16], encrypted[-16:]

    envelope = {
        "v": 1,
        "ts": _current_ms(),
        "iv": _b64url_encode(iv),
        "ct": _b64url_encode(ciphertext),
        "tag": _b64url_encode(tag),
    }

    return JSONResponse(
        content=envelope,
        status_code=status_code,
        headers={
            "X-DBX-Encrypted": "1",
            "Cache-Control": "no-store",
        },
    )
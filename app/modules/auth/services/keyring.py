"""PASETO v4.local keyring loader for auth v2.

Notes:
- `kid` is stored in token footer for key lookup.
- PASETO footer is authenticated but not encrypted by design.
- Rotation runbook:
  1) Add new key with new `kid` to AUTH_V2_SIGNING_KEYS_JSON.
  2) Switch AUTH_V2_CURRENT_KID to the new key.
  3) Keep old key as verify-only until max token TTL has elapsed.
  4) Retire old key (status=retired) after the window.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.settings import get_settings


@dataclass(frozen=True)
class KeyRecord:
    kid: str
    secret: str
    valid_from: Optional[datetime]
    valid_until: Optional[datetime]
    status: str

    @property
    def key_bytes(self) -> bytes:
        return hashlib.sha256(self.secret.encode("utf-8")).digest()

    def is_time_valid(self, now: datetime) -> bool:
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True

    def can_issue(self, now: datetime) -> bool:
        return self.status == "active" and self.is_time_valid(now)

    def can_verify(self, now: datetime) -> bool:
        return self.status in {"active", "verify_only"} and self.is_time_valid(now)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_records() -> Dict[str, KeyRecord]:
    raw = get_settings().AUTH_V2_SIGNING_KEYS_JSON
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AUTH_V2_SIGNING_KEYS_JSON must be valid JSON") from exc

    if not isinstance(parsed, list):
        raise RuntimeError("AUTH_V2_SIGNING_KEYS_JSON must be a JSON array")

    records: Dict[str, KeyRecord] = {}
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise RuntimeError(f"Invalid key entry at index {idx}")

        kid = str(item.get("kid", "")).strip()
        secret = str(item.get("secret", "")).strip()
        status = str(item.get("status", "retired")).strip().lower()
        if not kid or not secret:
            raise RuntimeError(f"Key entry at index {idx} is missing kid or secret")

        records[kid] = KeyRecord(
            kid=kid,
            secret=secret,
            valid_from=_parse_dt(item.get("valid_from")),
            valid_until=_parse_dt(item.get("valid_until")),
            status=status,
        )

    return records


def list_keys() -> List[KeyRecord]:
    return list(_load_records().values())


def get_current_key() -> KeyRecord:
    settings = get_settings()
    kid = settings.AUTH_V2_CURRENT_KID
    now = datetime.now(timezone.utc)
    record = _load_records().get(kid)
    if record is None:
        raise RuntimeError(f"Current key id {kid!r} not found in AUTH_V2_SIGNING_KEYS_JSON")
    if not record.can_issue(now):
        raise RuntimeError(f"Current key id {kid!r} is not active for issuance")
    return record


def get_key_for_kid(kid: str) -> KeyRecord:
    now = datetime.now(timezone.utc)
    record = _load_records().get(kid)
    if record is None:
        raise RuntimeError(f"Unknown kid: {kid}")
    if not record.can_verify(now):
        raise RuntimeError(f"kid {kid!r} is retired or outside verification window")
    return record

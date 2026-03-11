"""Device fingerprint derivation for refresh session binding."""

from __future__ import annotations

import hashlib
import re

from fastapi import Request

from controllers.auth_v2.constants import HEADER_APP_VERSION


def _ua_family(ua: str) -> str:
    if not ua:
        return "unknown"
    ua_lower = ua.lower()
    if "chrome/" in ua_lower and "edg/" not in ua_lower:
        return "chrome"
    if "firefox/" in ua_lower:
        return "firefox"
    if "safari/" in ua_lower and "chrome/" not in ua_lower:
        return "safari"
    if "edg/" in ua_lower:
        return "edge"
    if "postman" in ua_lower:
        return "postman"
    return ua_lower.split("/", 1)[0][:40]


def _platform(ua: str) -> str:
    ua_lower = ua.lower()
    if "android" in ua_lower:
        return "android"
    if "iphone" in ua_lower or "ipad" in ua_lower or "ios" in ua_lower:
        return "ios"
    if "windows" in ua_lower:
        return "windows"
    if "mac os" in ua_lower or "macintosh" in ua_lower:
        return "macos"
    if "linux" in ua_lower:
        return "linux"
    return "unknown"


def normalized_user_agent(ua: str) -> str:
    return re.sub(r"\s+", " ", ua or "").strip()[:500]


def compute_device_fingerprint(request: Request) -> str:
    ua = normalized_user_agent(request.headers.get("User-Agent", ""))
    app_version = (request.headers.get(HEADER_APP_VERSION, "") or "unknown").strip()[:64]
    normalized = f"{_ua_family(ua)}|{_platform(ua)}|{app_version}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

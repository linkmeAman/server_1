"""Shared test helpers for auth v2 endpoint tests."""

from __future__ import annotations

from queue import Queue
from threading import Thread

from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from app.modules.auth.router import router as auth_v2_router
from app.core.settings import get_settings


def testclient_requests_work() -> bool:
    probe_app = FastAPI()

    @probe_app.get("/__probe")
    def _probe():
        return {"ok": True}

    result = Queue(maxsize=1)

    def _run_probe():
        try:
            client = TestClient(probe_app)
            try:
                response = client.get("/__probe")
            finally:
                client.close()
            result.put(response.status_code == 200)
        except Exception:
            result.put(False)

    thread = Thread(target=_run_probe, daemon=True)
    thread.start()
    thread.join(timeout=2.0)
    if thread.is_alive() or result.empty():
        return False
    return bool(result.get())


def ensure_auth_v2_routes() -> None:
    paths = {route.path for route in main.app.router.routes}
    if "/auth/v2/check-contact" not in paths:
        main.app.include_router(auth_v2_router)


def build_headers(extra: dict | None = None) -> dict:
    headers = {}
    settings = get_settings()
    if settings.API_KEY_ENABLED and settings.API_KEYS:
        headers["X-API-Key"] = settings.API_KEYS[0]
    if extra:
        headers.update(extra)
    return headers


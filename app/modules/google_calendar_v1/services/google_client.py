"""Async Google Calendar API client for Google Calendar V1 routes."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import httpx

from core.settings import get_settings

from ..dependencies import GoogleCalendarError


class GoogleCalendarClient:
    """Small wrapper around Google Calendar v3 event APIs."""

    def __init__(self):
        settings = get_settings()
        self.base_url = settings.GOOGLE_CALENDAR_API_BASE_URL.rstrip("/")
        self.timeout_seconds = float(settings.GOOGLE_CALENDAR_TIMEOUT_SECONDS)

    async def _request(
        self,
        method: str,
        path: str,
        google_access_token: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {google_access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
            ) as client:
                response = await client.request(
                    method=method,
                    url=path,
                    params=params,
                    headers=headers,
                    json=json_body,
                )
        except httpx.RequestError as exc:
            raise GoogleCalendarError(
                code="GCAL_UPSTREAM_ERROR",
                message="Failed to reach Google Calendar API",
                status_code=502,
                data={"reason": str(exc)},
            ) from exc

        payload: Dict[str, Any]
        if not response.text:
            payload = {}
        else:
            try:
                parsed = response.json()
                payload = parsed if isinstance(parsed, dict) else {"data": parsed}
            except Exception:
                payload = {"raw": response.text}

        return response.status_code, payload

    async def create_event(
        self,
        calendar_id: str,
        event: Dict[str, Any],
        google_access_token: str,
    ) -> Tuple[int, Dict[str, Any]]:
        encoded_calendar_id = quote(calendar_id, safe="")
        return await self._request(
            method="POST",
            path=f"/calendars/{encoded_calendar_id}/events",
            google_access_token=google_access_token,
            params={"sendUpdates": "none"},
            json_body=event,
        )

    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        event: Dict[str, Any],
        google_access_token: str,
    ) -> Tuple[int, Dict[str, Any]]:
        encoded_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(event_id, safe="")
        return await self._request(
            method="PUT",
            path=f"/calendars/{encoded_calendar_id}/events/{encoded_event_id}",
            google_access_token=google_access_token,
            params={"sendUpdates": "none"},
            json_body=event,
        )

    async def list_instances(
        self,
        calendar_id: str,
        event_id: str,
        google_access_token: str,
    ) -> Tuple[int, Dict[str, Any]]:
        encoded_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(event_id, safe="")
        return await self._request(
            method="GET",
            path=f"/calendars/{encoded_calendar_id}/events/{encoded_event_id}/instances",
            google_access_token=google_access_token,
        )

    async def delete_event(
        self,
        calendar_id: str,
        event_id: str,
        google_access_token: str,
    ) -> Tuple[int, Dict[str, Any]]:
        encoded_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(event_id, safe="")
        return await self._request(
            method="DELETE",
            path=f"/calendars/{encoded_calendar_id}/events/{encoded_event_id}",
            google_access_token=google_access_token,
            params={"sendUpdates": "none"},
        )

"""Google My Business API client.

Wraps the GMB Business Information + My Business Reviews API via httpx.
Uses OAuth 2.0 access tokens managed by GmbTokenManager.

GMB Review API reference:
  https://developers.google.com/my-business/reference/rest/v4/accounts.locations.reviews
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.settings import get_settings

from ..dependencies import GoogleReviewsError

logger = logging.getLogger(__name__)

# GMB APIs use two different base URLs
_ACCOUNT_MGMT_BASE = "https://mybusinessaccountmanagement.googleapis.com/v1"
_REVIEWS_BASE = "https://mybusiness.googleapis.com/v4"


class GmbApiClient:
    """Async HTTP client for Google My Business Account/Reviews APIs."""

    def __init__(self) -> None:
        settings = get_settings()
        self.timeout = float(getattr(settings, "GMB_TIMEOUT_SECONDS", 30))

    # ------------------------------------------------------------------
    # Low-level request helper
    # ------------------------------------------------------------------

    async def _get(
        self,
        base_url: str,
        path: str,
        access_token: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=httpx.Timeout(self.timeout),
            ) as client:
                response = await client.get(path, headers=headers, params=params or {})
        except httpx.RequestError as exc:
            raise GoogleReviewsError(
                code="GMB_UPSTREAM_ERROR",
                message="Failed to reach Google My Business API",
                status_code=502,
                data={"reason": str(exc)},
            ) from exc

        payload: Dict[str, Any]
        try:
            parsed = response.json()
            payload = parsed if isinstance(parsed, dict) else {"data": parsed}
        except Exception:
            payload = {"raw": response.text}

        return response.status_code, payload

    async def _put(
        self,
        base_url: str,
        path: str,
        access_token: str,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=httpx.Timeout(self.timeout),
            ) as client:
                response = await client.put(path, headers=headers, json=json_body or {})
        except httpx.RequestError as exc:
            raise GoogleReviewsError(
                code="GMB_UPSTREAM_ERROR",
                message="Failed to reach Google My Business API",
                status_code=502,
                data={"reason": str(exc)},
            ) from exc

        payload: Dict[str, Any]
        try:
            parsed = response.json()
            payload = parsed if isinstance(parsed, dict) else {"data": parsed}
        except Exception:
            payload = {"raw": response.text}

        return response.status_code, payload

    async def _delete(
        self,
        base_url: str,
        path: str,
        access_token: str,
    ) -> Tuple[int, Dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=httpx.Timeout(self.timeout),
            ) as client:
                response = await client.delete(path, headers=headers)
        except httpx.RequestError as exc:
            raise GoogleReviewsError(
                code="GMB_UPSTREAM_ERROR",
                message="Failed to reach Google My Business API",
                status_code=502,
                data={"reason": str(exc)},
            ) from exc

        payload: Dict[str, Any]
        try:
            parsed = response.json()
            payload = parsed if isinstance(parsed, dict) else {"data": parsed}
        except Exception:
            payload = {"raw": response.text}

        return response.status_code, payload

    # ------------------------------------------------------------------
    # Account / Location helpers
    # ------------------------------------------------------------------

    async def list_accounts(self, access_token: str) -> List[Dict[str, Any]]:
        """Return list of GMB accounts accessible by the token."""
        status, payload = await self._get(_ACCOUNT_MGMT_BASE, "/accounts", access_token)
        if status != 200:
            raise GoogleReviewsError(
                code="GMB_ACCOUNTS_ERROR",
                message=f"Failed to list GMB accounts: {payload.get('error', {}).get('message', 'unknown')}",
                status_code=502,
            )
        return payload.get("accounts") or []

    async def list_locations(self, account_name: str, access_token: str) -> List[Dict[str, Any]]:
        """Return all locations for a given account resource name."""
        path = f"/{account_name}/locations"
        params = {"readMask": "name,title,storefrontAddress,metadata"}
        status, payload = await self._get(_ACCOUNT_MGMT_BASE, path, access_token, params)
        if status != 200:
            raise GoogleReviewsError(
                code="GMB_LOCATIONS_ERROR",
                message=f"Failed to list locations: {payload.get('error', {}).get('message', 'unknown')}",
                status_code=502,
            )
        return payload.get("locations") or []

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------

    async def fetch_reviews_page(
        self,
        account_name: str,
        location_name: str,
        access_token: str,
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch one page of reviews; returns (reviews, next_page_token)."""
        # GMB v4 reviews path: /{account_name}/{location_name}/reviews
        path = f"/{account_name}/{location_name}/reviews"
        params: Dict[str, Any] = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token

        status, payload = await self._get(_REVIEWS_BASE, path, access_token, params)
        if status != 200:
            raise GoogleReviewsError(
                code="GMB_REVIEWS_ERROR",
                message=f"Failed to fetch reviews: {payload.get('error', {}).get('message', 'unknown')}",
                status_code=502,
            )
        reviews = payload.get("reviews") or []
        next_token: Optional[str] = payload.get("nextPageToken")
        return reviews, next_token

    async def fetch_all_reviews(
        self,
        account_name: str,
        location_name: str,
        access_token: str,
    ) -> List[Dict[str, Any]]:
        """Auto-paginate and return all reviews for a location."""
        all_reviews: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        while True:
            page, page_token = await self.fetch_reviews_page(
                account_name, location_name, access_token, page_token
            )
            all_reviews.extend(page)
            logger.debug("Fetched %d reviews (total so far: %d)", len(page), len(all_reviews))
            if not page_token:
                break

        return all_reviews

    async def update_review_reply(
        self,
        review_name: str,
        access_token: str,
        reply_text: str,
    ) -> Dict[str, Any]:
        """Create or update the business reply for a review."""
        path = f"/{review_name}/reply"
        status, payload = await self._put(
            _REVIEWS_BASE,
            path,
            access_token,
            json_body={"comment": reply_text},
        )
        if status != 200:
            raise GoogleReviewsError(
                code="GMB_REPLY_ERROR",
                message=f"Failed to update review reply: {payload.get('error', {}).get('message', 'unknown')}",
                status_code=502,
                data={"upstream_status": status, "upstream_payload": payload},
            )
        return payload

    async def delete_review_reply(
        self,
        review_name: str,
        access_token: str,
    ) -> Dict[str, Any]:
        """Delete the business reply for a review."""
        path = f"/{review_name}/reply"
        status, payload = await self._delete(_REVIEWS_BASE, path, access_token)
        if status not in (200, 204):
            raise GoogleReviewsError(
                code="GMB_REPLY_DELETE_ERROR",
                message=f"Failed to delete review reply: {payload.get('error', {}).get('message', 'unknown')}",
                status_code=502,
                data={"upstream_status": status, "upstream_payload": payload},
            )
        return payload

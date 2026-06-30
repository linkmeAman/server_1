"""Handlers: 
  GET  /api/google-reviews/v1/discover  — list GMB accounts + locations from the API
  POST /api/google-reviews/v1/locations — register a GMB location into the DB
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_main_db_session
from app.core.response import error_response, success_response
from app.modules.google_reviews.dependencies import (
    GoogleReviewsError,
    has_google_reviews_permission,
    require_auth,
)
from app.modules.google_reviews.models.db import GoogleReviewLocation
from app.modules.google_reviews.schemas.models import LocationOut
from app.modules.google_reviews.services.gmb_client import GmbApiClient
from app.modules.google_reviews.services.gmb_token_manager import GmbTokenManager

router = APIRouter()

_token_manager = GmbTokenManager()
_gmb_client = GmbApiClient()


def _err(exc: GoogleReviewsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(error=exc.code, message=exc.message, data=exc.data).model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# GET /locations — list registered locations from the DB
# ---------------------------------------------------------------------------

@router.get("/locations")
async def list_locations(
    request: Request,
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        claims = require_auth(request.headers.get("Authorization"))
        can_read_all_reviews = await has_google_reviews_permission(claims, "reviews:read_all")
        if not can_read_all_reviews:
            raise GoogleReviewsError(
                code="REVIEWS_LOCATIONS_FORBIDDEN",
                message="You are not authorized to view all locations",
                status_code=403,
            )
        stmt = (
            select(GoogleReviewLocation)
            .where(GoogleReviewLocation.is_active.is_(True))
            .order_by(GoogleReviewLocation.display_name)
        )
        result = await db.execute(stmt)
        locations = result.scalars().all()
        return success_response(
            data={"locations": [LocationOut.model_validate(loc).model_dump(mode="json") for loc in locations]},
            message="Locations fetched",
        ).model_dump(mode="json")
    except GoogleReviewsError as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# GET /discover — query GMB API and return all accessible accounts + locations
# ---------------------------------------------------------------------------

@router.get("/discover")
async def discover_gmb_locations(request: Request):
    """Call the GMB API and return all accounts and locations the OAuth token can see.

    This is a diagnostic / setup endpoint — use it to find the correct
    account_name and location_name values to register via POST /locations.
    """
    try:
        claims = require_auth(request.headers.get("Authorization"))
        can_setup_locations = await has_google_reviews_permission(
            claims,
            "reviews:setup_locations",
        )
        if not can_setup_locations:
            raise GoogleReviewsError(
                code="REVIEWS_DISCOVER_FORBIDDEN",
                message="You are not authorized to discover Google review locations",
                status_code=403,
            )
        access_token = await _token_manager.get_valid_access_token()

        accounts = await _gmb_client.list_accounts(access_token)
        result = []
        for account in accounts:
            account_name = account.get("name", "")
            locations = await _gmb_client.list_locations(account_name, access_token)
            result.append({
                "account_name": account_name,
                "account_type": account.get("type"),
                "account_display_name": account.get("accountName") or account.get("name"),
                "locations": [
                    {
                        "location_name": loc.get("name", ""),
                        "display_name": loc.get("title") or loc.get("name", ""),
                        "address": _format_address(loc.get("storefrontAddress")),
                        "place_id": (loc.get("metadata") or {}).get("placeId"),
                    }
                    for loc in locations
                ],
            })

        return success_response(
            data={"accounts": result, "total_locations": sum(len(a["locations"]) for a in result)},
            message=f"Discovered {len(accounts)} account(s) from Google My Business API",
        ).model_dump(mode="json")

    except GoogleReviewsError as exc:
        return _err(exc)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_response(
                error="GMB_DISCOVER_ERROR",
                message=f"Discovery failed: {exc}",
            ).model_dump(mode="json"),
        )


def _format_address(addr: dict | None) -> str | None:
    if not addr:
        return None
    parts = []
    for line in addr.get("addressLines") or []:
        if line:
            parts.append(line)
    if addr.get("locality"):
        parts.append(addr["locality"])
    if addr.get("administrativeArea"):
        parts.append(addr["administrativeArea"])
    return ", ".join(parts) or None


# ---------------------------------------------------------------------------
# POST /locations — register a GMB location into google_review_locations
# ---------------------------------------------------------------------------

class RegisterLocationRequest(BaseModel):
    account_name: str
    location_name: str
    display_name: str
    address: str | None = None
    place_id: str | None = None
    review_url: str | None = None


class UpdateLocationReviewLinkRequest(BaseModel):
    review_url: str | None = None


@router.post("/locations")
async def register_location(
    payload: RegisterLocationRequest,
    request: Request,
    db: AsyncSession = Depends(get_main_db_session),
):
    """Register a GMB location into the DB so it can be synced."""
    try:
        claims = require_auth(request.headers.get("Authorization"))
        can_setup_locations = await has_google_reviews_permission(
            claims,
            "reviews:setup_locations",
        )
        if not can_setup_locations:
            raise GoogleReviewsError(
                code="REVIEWS_REGISTER_FORBIDDEN",
                message="You are not authorized to register Google review locations",
                status_code=403,
            )

        # Check for duplicate
        stmt = select(GoogleReviewLocation).where(
            GoogleReviewLocation.location_name == payload.location_name
        )
        existing_result = await db.execute(stmt)
        existing = existing_result.scalar_one_or_none()

        if existing:
            # Re-activate if it was soft-deleted
            if not existing.is_active:
                existing.is_active = True
                existing.display_name = payload.display_name
                existing.address = payload.address
                existing.place_id = payload.place_id
                existing.review_url = payload.review_url
                await db.commit()
                await db.refresh(existing)
                return success_response(
                    data=LocationOut.model_validate(existing).model_dump(mode="json"),
                    message="Location re-activated",
                ).model_dump(mode="json")

            return success_response(
                data=LocationOut.model_validate(existing).model_dump(mode="json"),
                message="Location already registered",
            ).model_dump(mode="json")

        loc = GoogleReviewLocation(
            account_name=payload.account_name,
            location_name=payload.location_name,
            display_name=payload.display_name,
            address=payload.address,
            place_id=payload.place_id,
            review_url=payload.review_url,
            is_active=True,
        )
        db.add(loc)
        await db.commit()
        await db.refresh(loc)

        return success_response(
            data=LocationOut.model_validate(loc).model_dump(mode="json"),
            message=f"Location '{payload.display_name}' registered successfully",
        ).model_dump(mode="json")

    except GoogleReviewsError as exc:
        return _err(exc)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_response(
                error="REVIEWS_REGISTER_ERROR",
                message=f"Failed to register location: {exc}",
            ).model_dump(mode="json"),
        )


@router.patch("/locations/{location_id}/review-link")
async def update_location_review_link(
    payload: UpdateLocationReviewLinkRequest,
    request: Request,
    location_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        claims = require_auth(request.headers.get("Authorization"))
        can_setup_locations = await has_google_reviews_permission(
            claims,
            "reviews:setup_locations",
        )
        if not can_setup_locations:
            raise GoogleReviewsError(
                code="REVIEWS_LOCATION_UPDATE_FORBIDDEN",
                message="You are not authorized to update Google review locations",
                status_code=403,
            )

        stmt = select(GoogleReviewLocation).where(GoogleReviewLocation.id == location_id)
        result = await db.execute(stmt)
        location = result.scalar_one_or_none()
        if not location:
            raise GoogleReviewsError(
                code="REVIEWS_LOCATION_NOT_FOUND",
                message="Google review location not found",
                status_code=404,
            )

        cleaned_review_url = (payload.review_url or "").strip() or None
        location.review_url = cleaned_review_url
        await db.commit()
        await db.refresh(location)

        return success_response(
            data=LocationOut.model_validate(location).model_dump(mode="json"),
            message="Location review link updated",
        ).model_dump(mode="json")
    except GoogleReviewsError as exc:
        return _err(exc)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_response(
                error="REVIEWS_LOCATION_UPDATE_ERROR",
                message=f"Failed to update location review link: {exc}",
            ).model_dump(mode="json"),
        )

"""Handler: POST /api/google-reviews/v1/sync — trigger review sync for a location."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_main_db_session
from app.core.response import error_response, success_response
from app.modules.google_reviews.dependencies import (
    GoogleReviewsError,
    has_google_reviews_permission,
    require_auth,
)
from app.modules.google_reviews.schemas.models import SyncRequest
from app.modules.google_reviews.services.sync_service import SyncService

router = APIRouter()

# Stateless singleton
_sync_service = SyncService()


def _err(exc: GoogleReviewsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(error=exc.code, message=exc.message, data=exc.data).model_dump(mode="json"),
    )


@router.post("/sync")
async def trigger_sync(
    payload: SyncRequest,
    request: Request,
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        claims = require_auth(request.headers.get("Authorization"))
        can_sync_reviews = await has_google_reviews_permission(claims, "reviews:sync")
        if not can_sync_reviews:
            raise GoogleReviewsError(
                code="REVIEWS_SYNC_FORBIDDEN",
                message="You are not authorized to sync Google reviews",
                status_code=403,
            )

        if payload.location_id is None:
            can_read_all_reviews = await has_google_reviews_permission(claims, "reviews:read_all")
            if not can_read_all_reviews:
                raise GoogleReviewsError(
                    code="REVIEWS_SYNC_ALL_FORBIDDEN",
                    message="You are not authorized to sync all locations",
                    status_code=403,
                )
            result = await _sync_service.sync_all_locations(db=db)
        else:
            result = await _sync_service.sync_location(location_id=payload.location_id, db=db)
        return success_response(
            data=result.model_dump(),
            message=result.message,
        ).model_dump(mode="json")
    except GoogleReviewsError as exc:
        return _err(exc)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_response(
                error="REVIEWS_SYNC_ERROR",
                message=f"Sync failed: {exc}",
            ).model_dump(mode="json"),
        )

"""Handler: GET /api/google-reviews/v1/locations — list tracked GMB locations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_main_db_session
from app.core.response import error_response, success_response
from app.modules.google_reviews.dependencies import GoogleReviewsError, require_auth
from app.modules.google_reviews.models.db import GoogleReviewLocation
from app.modules.google_reviews.schemas.models import LocationOut

router = APIRouter()


def _err(exc: GoogleReviewsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(error=exc.code, message=exc.message, data=exc.data).model_dump(mode="json"),
    )


@router.get("/locations")
async def list_locations(
    request: Request,
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        require_auth(request.headers.get("Authorization"))
        stmt = select(GoogleReviewLocation).where(GoogleReviewLocation.is_active.is_(True)).order_by(GoogleReviewLocation.display_name)
        result = await db.execute(stmt)
        locations = result.scalars().all()
        return success_response(
            data={"locations": [LocationOut.model_validate(loc).model_dump(mode="json") for loc in locations]},
            message="Locations fetched",
        ).model_dump(mode="json")
    except GoogleReviewsError as exc:
        return _err(exc)

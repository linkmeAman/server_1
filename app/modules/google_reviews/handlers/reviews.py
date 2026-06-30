"""Handler: GET /api/google-reviews/v1/reviews - paginated reviews list."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_main_db_session
from app.core.response import error_response, success_response
from app.modules.google_reviews.dependencies import (
    GoogleReviewsError,
    has_google_reviews_permission,
    require_auth,
)
from app.modules.google_reviews.models.db import GoogleReview, ReviewAnalysis
from app.modules.google_reviews.schemas.models import ReviewOut, ReviewsListResponse

router = APIRouter()


def _err(exc: GoogleReviewsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            error=exc.code,
            message=exc.message,
            data=exc.data,
        ).model_dump(mode="json"),
    )


@router.get("/reviews")
async def list_reviews(
    request: Request,
    location_id: Optional[int] = Query(None, ge=1),
    rating: Optional[int] = Query(None, ge=1, le=5),
    sentiment: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        claims = require_auth(request.headers.get("Authorization"))
        can_read_reviews = await has_google_reviews_permission(claims, "reviews:read")
        if not can_read_reviews:
            raise GoogleReviewsError(
                code="REVIEWS_FORBIDDEN",
                message="You are not authorized to view Google reviews",
                status_code=403,
            )

        filters = []
        if location_id:
            filters.append(GoogleReview.location_id == location_id)
        if rating:
            filters.append(GoogleReview.rating == rating)
        if date_from:
            filters.append(GoogleReview.review_time >= date_from)
        if date_to:
            filters.append(GoogleReview.review_time <= date_to)
        if sentiment:
            filters.append(ReviewAnalysis.sentiment == sentiment)
        if status == "replied":
            filters.append(GoogleReview.reply_text.is_not(None))
        elif status == "pending":
            filters.append(GoogleReview.reply_text.is_(None))

        count_stmt = select(func.count(GoogleReview.id))
        if sentiment:
            count_stmt = count_stmt.join(
                ReviewAnalysis,
                ReviewAnalysis.review_id == GoogleReview.id,
                isouter=True,
            )
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        count_result = await db.execute(count_stmt)
        total = count_result.scalar_one()

        offset = (page - 1) * per_page
        fetch_stmt = (
            select(GoogleReview)
            .options(
                selectinload(GoogleReview.analysis),
            )
            .order_by(GoogleReview.review_time.desc())
            .offset(offset)
            .limit(per_page)
        )
        if sentiment:
            fetch_stmt = fetch_stmt.join(
                ReviewAnalysis,
                ReviewAnalysis.review_id == GoogleReview.id,
                isouter=True,
            )
        if filters:
            fetch_stmt = fetch_stmt.where(and_(*filters))

        rows_result = await db.execute(fetch_stmt)
        rows = rows_result.scalars().all()

        items = [ReviewOut.model_validate(row).model_dump(mode="json") for row in rows]
        pages = math.ceil(total / per_page) if per_page else 1

        return success_response(
            data=ReviewsListResponse(
                items=items,  # type: ignore[arg-type]
                total=total,
                page=page,
                per_page=per_page,
                pages=pages,
            ).model_dump(mode="json"),
            message="Reviews fetched",
        ).model_dump(mode="json")
    except GoogleReviewsError as exc:
        return _err(exc)

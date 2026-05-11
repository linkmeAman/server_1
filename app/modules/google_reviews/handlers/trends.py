"""Handler: GET /api/google-reviews/v1/analytics/trends — time-series trend data."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_main_db_session
from app.core.response import error_response, success_response
from app.modules.google_reviews.dependencies import GoogleReviewsError, require_auth
from app.modules.google_reviews.models.db import GoogleReview, ReviewAnalysis
from app.modules.google_reviews.schemas.models import TrendPoint, TrendsOut

router = APIRouter()


def _err(exc: GoogleReviewsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(error=exc.code, message=exc.message, data=exc.data).model_dump(mode="json"),
    )


@router.get("/analytics/trends")
async def get_trends(
    request: Request,
    location_id: Optional[int] = Query(None, ge=1),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    group_by: str = Query("month", regex="^(week|month)$"),
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        require_auth(request.headers.get("Authorization"))

        filters = []
        if location_id:
            filters.append(GoogleReview.location_id == location_id)
        if date_from:
            filters.append(GoogleReview.review_time >= date_from)
        if date_to:
            filters.append(GoogleReview.review_time <= date_to)
        # Only include reviews with a timestamp
        filters.append(GoogleReview.review_time.isnot(None))

        where_clause = and_(*filters)

        year_col = extract("year", GoogleReview.review_time).label("yr")
        if group_by == "week":
            period_col = extract("week", GoogleReview.review_time).label("period_num")
        else:
            period_col = extract("month", GoogleReview.review_time).label("period_num")

        stmt = (
            select(
                year_col,
                period_col,
                func.count(GoogleReview.id).label("total_reviews"),
                func.avg(GoogleReview.rating).label("avg_rating"),
                func.sum(
                    case((ReviewAnalysis.sentiment == "positive", 1), else_=0)
                ).label("positive"),
                func.sum(
                    case((ReviewAnalysis.sentiment == "negative", 1), else_=0)
                ).label("negative"),
                func.sum(
                    case((ReviewAnalysis.sentiment == "neutral", 1), else_=0)
                ).label("neutral"),
                func.sum(
                    case((ReviewAnalysis.sentiment == "mixed", 1), else_=0)
                ).label("mixed"),
            )
            .join(ReviewAnalysis, ReviewAnalysis.review_id == GoogleReview.id, isouter=True)
            .where(where_clause)
            .group_by("yr", "period_num")
            .order_by("yr", "period_num")
        )

        result = await db.execute(stmt)
        rows = result.mappings().all()

        trend_points: List[TrendPoint] = []
        for row in rows:
            yr = int(row["yr"])
            pn = int(row["period_num"])
            if group_by == "week":
                label = f"{yr}-W{pn:02d}"
            else:
                label = f"{yr}-{pn:02d}"

            trend_points.append(
                TrendPoint(
                    period=label,
                    total_reviews=int(row["total_reviews"] or 0),
                    avg_rating=round(float(row["avg_rating"] or 0), 2),
                    positive=int(row["positive"] or 0),
                    negative=int(row["negative"] or 0),
                    neutral=int(row["neutral"] or 0),
                    mixed=int(row["mixed"] or 0),
                )
            )

        return success_response(
            data=TrendsOut(
                location_id=location_id,
                group_by=group_by,
                data=trend_points,
            ).model_dump(mode="json"),
            message="Trends fetched",
        ).model_dump(mode="json")

    except GoogleReviewsError as exc:
        return _err(exc)

"""Handler: GET /api/google-reviews/v1/analytics — aggregate stats for a location."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_main_db_session
from app.core.response import error_response, success_response
from app.modules.google_reviews.dependencies import GoogleReviewsError, require_auth
from app.modules.google_reviews.models.db import GoogleReview, ReviewAnalysis
from app.modules.google_reviews.schemas.models import (
    AnalyticsOut,
    SentimentDistribution,
    TopTopic,
)

router = APIRouter()


def _err(exc: GoogleReviewsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(error=exc.code, message=exc.message, data=exc.data).model_dump(mode="json"),
    )


@router.get("/analytics")
async def get_analytics(
    request: Request,
    location_id: Optional[int] = Query(None, ge=1),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
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

        where_clause = and_(*filters) if filters else None

        # --- Core aggregates ---
        base_stmt = select(
            func.count(GoogleReview.id).label("total"),
            func.avg(GoogleReview.rating).label("avg_rating"),
            func.sum(
                func.cast(GoogleReview.reply_text.isnot(None), type_=func.count(GoogleReview.id).type)
            ).label("with_reply"),
        )
        if where_clause is not None:
            base_stmt = base_stmt.where(where_clause)
        agg_result = await db.execute(base_stmt)
        agg_row = agg_result.mappings().first() or {}

        total: int = int(agg_row.get("total") or 0)
        avg_rating: float = round(float(agg_row.get("avg_rating") or 0), 2)

        # Response rate: reviews with a reply / total
        with_reply_stmt = select(func.count(GoogleReview.id)).where(
            GoogleReview.reply_text.isnot(None)
        )
        if where_clause is not None:
            with_reply_stmt = with_reply_stmt.where(where_clause)
        with_reply_result = await db.execute(with_reply_stmt)
        with_reply = int(with_reply_result.scalar_one() or 0)
        response_rate = round((with_reply / total * 100) if total > 0 else 0.0, 1)

        # --- Rating breakdown ---
        rating_stmt = select(GoogleReview.rating, func.count(GoogleReview.id).label("cnt"))
        if where_clause is not None:
            rating_stmt = rating_stmt.where(where_clause)
        rating_stmt = rating_stmt.group_by(GoogleReview.rating)
        rating_result = await db.execute(rating_stmt)
        rating_breakdown: dict = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
        for r_row in rating_result.mappings().all():
            key = str(r_row["rating"])
            if key in rating_breakdown:
                rating_breakdown[key] = int(r_row["cnt"])

        # --- Sentiment distribution ---
        sent_stmt = (
            select(ReviewAnalysis.sentiment, func.count(ReviewAnalysis.id).label("cnt"))
            .join(GoogleReview, GoogleReview.id == ReviewAnalysis.review_id)
        )
        if where_clause is not None:
            sent_stmt = sent_stmt.where(where_clause)
        sent_stmt = sent_stmt.group_by(ReviewAnalysis.sentiment)
        sent_result = await db.execute(sent_stmt)
        sentiment_counts = {r["sentiment"]: int(r["cnt"]) for r in sent_result.mappings().all()}
        sentiment_dist = SentimentDistribution(
            positive=sentiment_counts.get("positive", 0),
            negative=sentiment_counts.get("negative", 0),
            neutral=sentiment_counts.get("neutral", 0),
            mixed=sentiment_counts.get("mixed", 0),
        )

        # --- Top topics (aggregate across all analyses for this filter) ---
        topics_stmt = (
            select(ReviewAnalysis.topics)
            .join(GoogleReview, GoogleReview.id == ReviewAnalysis.review_id)
        )
        if where_clause is not None:
            topics_stmt = topics_stmt.where(where_clause)
        topics_result = await db.execute(topics_stmt)
        topic_counter: Counter = Counter()
        for topics_row in topics_result.scalars().all():
            if isinstance(topics_row, list):
                for t in topics_row:
                    if t:
                        topic_counter[str(t)] += 1

        top_topics: List[TopTopic] = [
            TopTopic(topic=t, count=c) for t, c in topic_counter.most_common(15)
        ]

        analytics = AnalyticsOut(
            location_id=location_id,
            total_reviews=total,
            avg_rating=avg_rating,
            response_rate=response_rate,
            sentiment_distribution=sentiment_dist,
            rating_breakdown=rating_breakdown,
            top_topics=top_topics,
            date_from=date_from,
            date_to=date_to,
        )

        return success_response(
            data=analytics.model_dump(mode="json"),
            message="Analytics fetched",
        ).model_dump(mode="json")

    except GoogleReviewsError as exc:
        return _err(exc)

"""Sync service — fetches reviews from GMB and upserts them into the database.

Usage:
    service = SyncService()
    result = await service.sync_location(location_id=1, db=session)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.google_reviews.models.db import GoogleReview, GoogleReviewLocation
from app.modules.google_reviews.schemas.models import SyncResult

from .gmb_client import GmbApiClient
from .gmb_token_manager import GmbTokenManager
from .analysis_service import AnalysisService

logger = logging.getLogger(__name__)


def _parse_gmb_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 string from GMB API into a naive UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def _extract_star_rating(raw: Dict[str, Any]) -> int:
    """Map GMB starRating string to integer 1-5."""
    mapping = {
        "ONE": 1,
        "TWO": 2,
        "THREE": 3,
        "FOUR": 4,
        "FIVE": 5,
    }
    return mapping.get(str(raw.get("starRating", "")).upper(), 0)


class SyncService:
    """Orchestrates GMB API sync and DB upsert for a location."""

    def __init__(
        self,
        client: Optional[GmbApiClient] = None,
        token_manager: Optional[GmbTokenManager] = None,
        analysis_service: Optional[AnalysisService] = None,
    ) -> None:
        self.client = client or GmbApiClient()
        self.token_manager = token_manager or GmbTokenManager()
        self.analysis_service = analysis_service or AnalysisService()

    async def sync_location(self, location_id: int, db: AsyncSession) -> SyncResult:
        """Fetch all GMB reviews for a location, upsert to DB, run analysis.

        Returns a SyncResult with counts of new, updated, and analyzed reviews.
        """
        # 1) Load location record
        location = await db.get(GoogleReviewLocation, location_id)
        if location is None:
            from app.modules.google_reviews.dependencies import GoogleReviewsError
            raise GoogleReviewsError(
                code="REVIEWS_LOCATION_NOT_FOUND",
                message=f"Location with id={location_id} not found",
                status_code=404,
            )

        # 2) Fetch from GMB
        access_token = await self.token_manager.get_valid_access_token()
        raw_reviews = await self.client.fetch_all_reviews(
            account_name=location.account_name,
            location_name=location.location_name,
            access_token=access_token,
        )
        logger.info(
            "Fetched %d reviews from GMB for location_id=%d", len(raw_reviews), location_id
        )

        # 3) Upsert loop
        new_count = 0
        updated_count = 0
        review_db_ids: list[int] = []

        for raw in raw_reviews:
            review_id_str: str = raw.get("name", "")
            if not review_id_str:
                continue

            reviewer = raw.get("reviewer") or {}
            reply = raw.get("reviewReply") or {}

            # Check for existing row
            stmt = select(GoogleReview).where(GoogleReview.review_id == review_id_str)
            result = await db.execute(stmt)
            existing: Optional[GoogleReview] = result.scalar_one_or_none()

            if existing is None:
                row = GoogleReview(
                    review_id=review_id_str,
                    location_id=location_id,
                    author_name=reviewer.get("displayName"),
                    author_photo_url=reviewer.get("profilePhotoUrl"),
                    rating=_extract_star_rating(raw),
                    text=raw.get("comment"),
                    review_time=_parse_gmb_datetime(raw.get("createTime")),
                    reply_text=reply.get("comment"),
                    reply_time=_parse_gmb_datetime(reply.get("updateTime")),
                    raw_response=raw,
                    synced_at=datetime.utcnow(),
                )
                db.add(row)
                new_count += 1
            else:
                existing.author_name = reviewer.get("displayName")
                existing.author_photo_url = reviewer.get("profilePhotoUrl")
                existing.rating = _extract_star_rating(raw)
                existing.text = raw.get("comment")
                existing.review_time = _parse_gmb_datetime(raw.get("createTime"))
                existing.reply_text = reply.get("comment")
                existing.reply_time = _parse_gmb_datetime(reply.get("updateTime"))
                existing.raw_response = raw
                existing.synced_at = datetime.utcnow()
                updated_count += 1

        await db.flush()  # Get IDs for newly inserted rows

        # 4) Collect IDs needing analysis (new rows + rows with no analysis yet)
        stmt_ids = select(GoogleReview.id).where(GoogleReview.location_id == location_id)
        id_result = await db.execute(stmt_ids)
        all_ids = [row[0] for row in id_result.fetchall()]

        analyzed_count = await self.analysis_service.batch_analyze(all_ids, db)
        await db.commit()

        return SyncResult(
            location_id=location_id,
            new_count=new_count,
            updated_count=updated_count,
            analyzed_count=analyzed_count,
            message=(
                f"Sync complete: {new_count} new, {updated_count} updated, "
                f"{analyzed_count} analyzed."
            ),
        )

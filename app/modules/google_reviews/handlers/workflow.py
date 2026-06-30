"""Handlers for Google review reply management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

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
from app.modules.google_reviews.models.db import GoogleReview
from app.modules.google_reviews.schemas.models import ReplyActionResult, ReviewReplyRequest
from app.modules.google_reviews.services.gmb_client import GmbApiClient
from app.modules.google_reviews.services.gmb_token_manager import GmbTokenManager

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


def _parse_gmb_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


async def _require_reply_permission(claims: dict) -> None:
    can_reply = await has_google_reviews_permission(claims, "reviews:reply")
    if not can_reply:
        raise GoogleReviewsError(
            code="REVIEWS_REPLY_FORBIDDEN",
            message="You are not authorized to manage Google review replies",
            status_code=403,
        )


@router.post("/reviews/{review_db_id}/reply")
async def upsert_review_reply(
    review_db_id: int,
    payload: ReviewReplyRequest,
    request: Request,
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        claims = require_auth(request.headers.get("Authorization"))
        await _require_reply_permission(claims)

        review = await db.get(GoogleReview, review_db_id)
        if review is None:
            raise GoogleReviewsError(
                code="REVIEWS_NOT_FOUND",
                message="Review not found",
                status_code=404,
            )

        reply_text = payload.reply_text.strip()
        token_manager = GmbTokenManager()
        client = GmbApiClient()
        access_token = await token_manager.get_valid_access_token()

        api_response = await client.update_review_reply(
            review_name=review.review_id,
            access_token=access_token,
            reply_text=reply_text,
        )
        reply_meta = api_response.get("reviewReply") or api_response
        review.reply_text = reply_meta.get("comment", reply_text)
        review.reply_time = _parse_gmb_datetime(reply_meta.get("updateTime")) or datetime.utcnow()
        review.synced_at = datetime.utcnow()

        await db.commit()

        return success_response(
            data=ReplyActionResult(
                review_id=review.id,
                google_reply_updated=True,
                reply_text=review.reply_text,
                reply_time=review.reply_time,
            ).model_dump(mode="json"),
            message="Reply saved to Google",
        ).model_dump(mode="json")
    except GoogleReviewsError as exc:
        await db.rollback()
        return _err(exc)


@router.delete("/reviews/{review_db_id}/reply")
async def delete_review_reply(
    review_db_id: int,
    request: Request,
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        claims = require_auth(request.headers.get("Authorization"))
        await _require_reply_permission(claims)

        review = await db.get(GoogleReview, review_db_id)
        if review is None:
            raise GoogleReviewsError(
                code="REVIEWS_NOT_FOUND",
                message="Review not found",
                status_code=404,
            )
        if not review.reply_text:
            raise GoogleReviewsError(
                code="REVIEWS_REPLY_NOT_FOUND",
                message="This review does not have a Google reply to delete",
                status_code=404,
            )

        token_manager = GmbTokenManager()
        client = GmbApiClient()
        access_token = await token_manager.get_valid_access_token()
        api_response = await client.delete_review_reply(
            review_name=review.review_id,
            access_token=access_token,
        )

        review.reply_text = None
        review.reply_time = None
        review.synced_at = datetime.utcnow()

        await db.commit()

        return success_response(
            data=ReplyActionResult(
                review_id=review.id,
                google_reply_updated=True,
                reply_text=None,
                reply_time=None,
            ).model_dump(mode="json"),
            message="Reply deleted from Google",
        ).model_dump(mode="json")
    except GoogleReviewsError as exc:
        await db.rollback()
        return _err(exc)

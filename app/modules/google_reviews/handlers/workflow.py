"""Handlers for review assignment, reply submission, and leaderboard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_main_db_session
from app.core.response import error_response, success_response
from app.modules.google_reviews.dependencies import (
    GoogleReviewsError,
    has_google_reviews_permission,
    require_auth,
)
from app.modules.google_reviews.models.db import (
    GoogleReview,
    GoogleReviewAssignment,
    GoogleReviewAssignmentStatus,
    GoogleReviewReplyLog,
)
from app.modules.google_reviews.schemas.models import (
    CounselorLeaderboardResponse,
    CounselorLeaderboardRow,
    ReplyActionResult,
    ReviewAssignRequest,
    ReviewAssignmentOut,
    ReviewReplyRequest,
)
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


def _caller_name(claims: dict) -> Optional[str]:
    return (
        claims.get("display_name")
        or claims.get("name")
        or claims.get("fullname")
        or claims.get("mobile")
    )


@router.post("/reviews/{review_db_id}/assign")
async def assign_review(
    review_db_id: int,
    payload: ReviewAssignRequest,
    request: Request,
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        claims = require_auth(request.headers.get("Authorization"))
        review = await db.get(GoogleReview, review_db_id)
        if review is None:
            raise GoogleReviewsError(
                code="REVIEWS_NOT_FOUND",
                message="Review not found",
                status_code=404,
            )

        stmt = select(GoogleReviewAssignment).where(GoogleReviewAssignment.review_id == review_db_id)
        result = await db.execute(stmt)
        assignment = result.scalar_one_or_none()

        now = datetime.utcnow()
        if assignment is None:
            assignment = GoogleReviewAssignment(
                review_id=review.id,
                location_id=review.location_id,
                counselor_employee_id=payload.counselor_employee_id,
                counselor_name=payload.counselor_name,
                assigned_by_employee_id=claims.get("employee_id"),
                assigned_by_name=_caller_name(claims),
                assigned_at=now,
                status=GoogleReviewAssignmentStatus.replied if review.reply_text else GoogleReviewAssignmentStatus.assigned,
                last_action_at=now,
                notes=payload.notes,
            )
            db.add(assignment)
        else:
            assignment.counselor_employee_id = payload.counselor_employee_id
            assignment.counselor_name = payload.counselor_name
            assignment.assigned_by_employee_id = claims.get("employee_id")
            assignment.assigned_by_name = _caller_name(claims)
            assignment.assigned_at = now
            assignment.last_action_at = now
            assignment.notes = payload.notes
            if assignment.status == GoogleReviewAssignmentStatus.replied and not review.reply_text:
                assignment.status = GoogleReviewAssignmentStatus.assigned

        await db.commit()
        await db.refresh(assignment)
        return success_response(
            data={"assignment": ReviewAssignmentOut.model_validate(assignment).model_dump(mode="json")},
            message="Review assigned",
        ).model_dump(mode="json")
    except GoogleReviewsError as exc:
        await db.rollback()
        return _err(exc)


@router.post("/reviews/{review_db_id}/reply")
async def reply_to_review(
    review_db_id: int,
    payload: ReviewReplyRequest,
    request: Request,
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        claims = require_auth(request.headers.get("Authorization"))
        caller_employee_id = claims.get("employee_id")
        if caller_employee_id is None:
            raise GoogleReviewsError(
                code="REVIEWS_EMPLOYEE_CONTEXT_MISSING",
                message="Logged-in user is missing employee context",
                status_code=403,
            )

        review = await db.get(GoogleReview, review_db_id)
        if review is None:
            raise GoogleReviewsError(
                code="REVIEWS_NOT_FOUND",
                message="Review not found",
                status_code=404,
            )

        stmt = select(GoogleReviewAssignment).where(GoogleReviewAssignment.review_id == review_db_id)
        result = await db.execute(stmt)
        assignment = result.scalar_one_or_none()
        if assignment is None:
            raise GoogleReviewsError(
                code="REVIEWS_NOT_ASSIGNED",
                message="Review must be assigned before replying",
                status_code=400,
            )
        if int(assignment.counselor_employee_id) != int(caller_employee_id):
            raise GoogleReviewsError(
                code="REVIEWS_ASSIGNMENT_MISMATCH",
                message="This review is assigned to a different counselor",
                status_code=403,
            )

        assignment.status = GoogleReviewAssignmentStatus.reply_pending
        assignment.last_action_at = datetime.utcnow()
        await db.flush()

        token_manager = GmbTokenManager()
        client = GmbApiClient()
        access_token = await token_manager.get_valid_access_token()

        try:
            api_response = await client.update_review_reply(
                review_name=review.review_id,
                access_token=access_token,
                reply_text=payload.reply_text.strip(),
            )
            reply_meta = api_response.get("reviewReply") or api_response
            review.reply_text = reply_meta.get("comment", payload.reply_text.strip())
            review.reply_time = _parse_gmb_datetime(reply_meta.get("updateTime")) or datetime.utcnow()
            review.synced_at = datetime.utcnow()
            assignment.status = GoogleReviewAssignmentStatus.replied
            assignment.last_action_at = datetime.utcnow()

            db.add(
                GoogleReviewReplyLog(
                    review_id=review.id,
                    assignment_id=assignment.id,
                    counselor_employee_id=assignment.counselor_employee_id,
                    counselor_name=assignment.counselor_name,
                    reply_text=payload.reply_text.strip(),
                    google_api_status="success",
                    google_api_response=api_response,
                    is_success=True,
                )
            )
            await db.commit()

            return success_response(
                data=ReplyActionResult(
                    review_id=review.id,
                    assignment_id=assignment.id,
                    status=assignment.status,
                    google_reply_updated=True,
                    reply_text=review.reply_text,
                    reply_time=review.reply_time,
                ).model_dump(mode="json"),
                message="Reply posted to Google",
            ).model_dump(mode="json")
        except GoogleReviewsError as exc:
            assignment.status = GoogleReviewAssignmentStatus.reply_failed
            assignment.last_action_at = datetime.utcnow()
            db.add(
                GoogleReviewReplyLog(
                    review_id=review.id,
                    assignment_id=assignment.id,
                    counselor_employee_id=assignment.counselor_employee_id,
                    counselor_name=assignment.counselor_name,
                    reply_text=payload.reply_text.strip(),
                    google_api_status="failed",
                    google_api_response=exc.data,
                    is_success=False,
                )
            )
            await db.commit()
            raise exc
    except GoogleReviewsError as exc:
        await db.rollback()
        return _err(exc)


@router.get("/leaderboard")
async def get_leaderboard(
    request: Request,
    location_id: Optional[int] = Query(None, ge=1),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_main_db_session),
):
    try:
        claims = require_auth(request.headers.get("Authorization"))
        can_view_leaderboard = await has_google_reviews_permission(
            claims,
            "reviews:leaderboard:read",
        )
        if not can_view_leaderboard:
            raise GoogleReviewsError(
                code="REVIEWS_LEADERBOARD_FORBIDDEN",
                message="You are not authorized to view the counselor leaderboard",
                status_code=403,
            )

        filters = []
        if location_id:
            filters.append(GoogleReviewAssignment.location_id == location_id)
        if date_from:
            filters.append(GoogleReviewAssignment.assigned_at >= date_from)
        if date_to:
            filters.append(GoogleReviewAssignment.assigned_at <= date_to)

        stmt = select(
            GoogleReviewAssignment.counselor_employee_id,
            func.max(GoogleReviewAssignment.counselor_name).label("counselor_name"),
            func.count(GoogleReviewAssignment.id).label("assigned_reviews"),
            func.sum(
                case(
                    (GoogleReviewAssignment.status == GoogleReviewAssignmentStatus.replied, 1),
                    else_=0,
                )
            ).label("replied_reviews"),
            func.sum(
                case(
                    (GoogleReviewAssignment.status == GoogleReviewAssignmentStatus.reply_failed, 1),
                    else_=0,
                )
            ).label("failed_reviews"),
            func.sum(
                case(
                    (
                        GoogleReviewAssignment.status.in_(
                            [
                                GoogleReviewAssignmentStatus.assigned,
                                GoogleReviewAssignmentStatus.draft_saved,
                                GoogleReviewAssignmentStatus.reply_pending,
                            ]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("pending_reviews"),
        ).group_by(GoogleReviewAssignment.counselor_employee_id)
        if filters:
            stmt = stmt.where(and_(*filters))

        result = await db.execute(stmt)
        items = []
        for row in result.mappings().all():
            assigned_reviews = int(row["assigned_reviews"] or 0)
            replied_reviews = int(row["replied_reviews"] or 0)
            items.append(
                CounselorLeaderboardRow(
                    counselor_employee_id=int(row["counselor_employee_id"]),
                    counselor_name=row["counselor_name"],
                    assigned_reviews=assigned_reviews,
                    replied_reviews=replied_reviews,
                    pending_reviews=int(row["pending_reviews"] or 0),
                    failed_reviews=int(row["failed_reviews"] or 0),
                    reply_rate=round((replied_reviews / assigned_reviews * 100) if assigned_reviews else 0.0, 1),
                )
            )

        items.sort(key=lambda item: (-item.reply_rate, -item.replied_reviews, item.counselor_employee_id))

        return success_response(
            data=CounselorLeaderboardResponse(
                location_id=location_id,
                date_from=date_from,
                date_to=date_to,
                items=items,
            ).model_dump(mode="json"),
            message="Leaderboard fetched",
        ).model_dump(mode="json")
    except GoogleReviewsError as exc:
        return _err(exc)

"""SQLAlchemy ORM models for Google Reviews module.

Tables (all on main business DB — no __bind_key__ needed):
  - google_review_locations  : GMB-verified business locations being tracked
  - google_reviews           : Individual review records synced from GMB API
  - review_analysis          : Semantic analysis results (1:1 with google_reviews)
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SentimentLabel(str, enum.Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"
    mixed = "mixed"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class GoogleReviewLocation(Base):
    """A Google My Business location (center/branch) being monitored."""

    __tablename__ = "google_review_locations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # GMB API identifiers — format: "accounts/{account_id}/locations/{location_id}"
    account_name = Column(String(255), nullable=False, comment="GMB account resource name")
    location_name = Column(String(255), nullable=False, unique=True, comment="GMB location resource name")
    display_name = Column(String(255), nullable=False)
    address = Column(Text, nullable=True)
    place_id = Column(String(255), nullable=True, comment="Google Maps Place ID")
    review_url = Column(Text, nullable=True, comment="Optional custom Google review link from GBP dashboard")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    reviews = relationship("GoogleReview", back_populates="location", lazy="dynamic")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GoogleReviewLocation id={self.id} display_name={self.display_name!r}>"


class GoogleReview(Base):
    """A single review synced from the Google My Business API."""

    __tablename__ = "google_reviews"
    __table_args__ = (
        UniqueConstraint("review_id", name="uq_google_reviews_review_id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # GMB review resource name — format: "accounts/.../locations/.../reviews/{id}"
    review_id = Column(String(512), nullable=False, comment="GMB review resource name (unique)")
    location_id = Column(BigInteger, ForeignKey("google_review_locations.id", ondelete="CASCADE"), nullable=False)

    author_name = Column(String(255), nullable=True)
    author_photo_url = Column(Text, nullable=True)
    rating = Column(SmallInteger, nullable=False, comment="Star rating 1-5")
    text = Column(Text, nullable=True, comment="Review body text")
    review_time = Column(DateTime, nullable=True, comment="When the review was posted on Google")

    # Reply data (stored for future counselor reply feature)
    reply_text = Column(Text, nullable=True)
    reply_time = Column(DateTime, nullable=True)

    language = Column(String(10), nullable=True, comment="ISO 639-1 language code detected by langdetect")
    raw_response = Column(JSON, nullable=True, comment="Full GMB API response payload")
    synced_at = Column(DateTime, nullable=False, server_default=func.now(), comment="Last sync timestamp")

    location = relationship("GoogleReviewLocation", back_populates="reviews")
    analysis = relationship("ReviewAnalysis", back_populates="review", uselist=False)
    assignment = relationship("GoogleReviewAssignment", back_populates="review", uselist=False)
    reply_logs = relationship("GoogleReviewReplyLog", back_populates="review")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GoogleReview id={self.id} rating={self.rating} review_id={self.review_id!r}>"


class ReviewAnalysis(Base):
    """Semantic analysis results for a single review (1:1 with GoogleReview)."""

    __tablename__ = "review_analysis"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    review_id = Column(
        BigInteger,
        ForeignKey("google_reviews.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="FK to google_reviews.id",
    )

    sentiment = Column(
        Enum(SentimentLabel, name="sentiment_label"),
        nullable=False,
        comment="Overall sentiment label",
    )
    compound_score = Column(
        Float,
        nullable=False,
        comment="VADER compound score in [-1, +1]",
    )
    # transformer_label: POSITIVE / NEGATIVE from distilbert
    transformer_label = Column(String(32), nullable=True)
    transformer_score = Column(Float, nullable=True, comment="Transformer model confidence 0-1")

    topics = Column(JSON, nullable=True, comment="List of extracted topic strings from TF-IDF")
    keywords = Column(JSON, nullable=True, comment="Top weighted keyword strings for this review")
    model_version = Column(String(64), nullable=False, default="v1", comment="Analysis pipeline version")
    analyzed_at = Column(DateTime, nullable=False, server_default=func.now())

    review = relationship("GoogleReview", back_populates="analysis")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ReviewAnalysis review_id={self.review_id} sentiment={self.sentiment}>"


class GoogleReviewAssignmentStatus(str, enum.Enum):
    assigned = "assigned"
    draft_saved = "draft_saved"
    reply_pending = "reply_pending"
    replied = "replied"
    reply_failed = "reply_failed"


class GoogleReviewAssignment(Base):
    """Tracks the single counselor currently responsible for a review."""

    __tablename__ = "google_review_assignments"
    __table_args__ = (
        UniqueConstraint("review_id", name="uq_google_review_assignments_review_id"),
        Index("ix_google_review_assignments_counselor_employee_id", "counselor_employee_id"),
        Index("ix_google_review_assignments_location_id", "location_id"),
        Index("ix_google_review_assignments_status", "status"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    review_id = Column(
        BigInteger,
        ForeignKey("google_reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id = Column(
        BigInteger,
        ForeignKey("google_review_locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    counselor_employee_id = Column(BigInteger, nullable=False)
    counselor_name = Column(String(255), nullable=True)
    assigned_by_employee_id = Column(BigInteger, nullable=True)
    assigned_by_name = Column(String(255), nullable=True)
    assigned_at = Column(DateTime, nullable=False, server_default=func.now())
    status = Column(
        Enum(GoogleReviewAssignmentStatus, name="google_review_assignment_status"),
        nullable=False,
        default=GoogleReviewAssignmentStatus.assigned,
        server_default=GoogleReviewAssignmentStatus.assigned.value,
    )
    last_action_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    review = relationship("GoogleReview", back_populates="assignment")
    location = relationship("GoogleReviewLocation")
    reply_logs = relationship("GoogleReviewReplyLog", back_populates="assignment")


class GoogleReviewReplyLog(Base):
    """Auditable log of every attempted reply submission to Google."""

    __tablename__ = "google_review_reply_logs"
    __table_args__ = (
        Index("ix_google_review_reply_logs_review_id", "review_id"),
        Index("ix_google_review_reply_logs_assignment_id", "assignment_id"),
        Index("ix_google_review_reply_logs_counselor_employee_id", "counselor_employee_id"),
        Index("ix_google_review_reply_logs_created_at", "created_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    review_id = Column(
        BigInteger,
        ForeignKey("google_reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    assignment_id = Column(
        BigInteger,
        ForeignKey("google_review_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    counselor_employee_id = Column(BigInteger, nullable=False)
    counselor_name = Column(String(255), nullable=True)
    reply_text = Column(Text, nullable=False)
    google_api_status = Column(String(50), nullable=False)
    google_api_response = Column(JSON, nullable=True)
    is_success = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    review = relationship("GoogleReview", back_populates="reply_logs")
    assignment = relationship("GoogleReviewAssignment", back_populates="reply_logs")

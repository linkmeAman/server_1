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

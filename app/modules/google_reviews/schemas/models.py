"""Pydantic request/response schemas for Google Reviews API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

class SentimentLabel(str, Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"
    mixed = "mixed"


# ---------------------------------------------------------------------------
# Location schemas
# ---------------------------------------------------------------------------

class LocationOut(BaseModel):
    id: int
    account_name: str
    location_name: str
    display_name: str
    address: Optional[str]
    place_id: Optional[str]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Sync schemas
# ---------------------------------------------------------------------------

class SyncRequest(BaseModel):
    location_id: int = Field(..., ge=1, description="ID of the GoogleReviewLocation to sync")


class SyncResult(BaseModel):
    location_id: int
    new_count: int
    updated_count: int
    analyzed_count: int
    message: str


# ---------------------------------------------------------------------------
# Review schemas
# ---------------------------------------------------------------------------

class ReviewAnalysisOut(BaseModel):
    sentiment: SentimentLabel
    compound_score: float
    transformer_label: Optional[str]
    transformer_score: Optional[float]
    topics: Optional[List[str]]
    keywords: Optional[List[str]]
    model_version: str
    analyzed_at: datetime

    class Config:
        from_attributes = True


class ReviewOut(BaseModel):
    id: int
    review_id: str
    location_id: int
    author_name: Optional[str]
    author_photo_url: Optional[str]
    rating: int
    text: Optional[str]
    review_time: Optional[datetime]
    reply_text: Optional[str]
    reply_time: Optional[datetime]
    language: Optional[str]
    synced_at: datetime
    analysis: Optional[ReviewAnalysisOut]

    class Config:
        from_attributes = True


class ReviewsListResponse(BaseModel):
    items: List[ReviewOut]
    total: int
    page: int
    per_page: int
    pages: int


# ---------------------------------------------------------------------------
# Analytics schemas
# ---------------------------------------------------------------------------

class SentimentDistribution(BaseModel):
    positive: int
    negative: int
    neutral: int
    mixed: int


class RatingBreakdown(BaseModel):
    one: int = Field(alias="1")
    two: int = Field(alias="2")
    three: int = Field(alias="3")
    four: int = Field(alias="4")
    five: int = Field(alias="5")

    class Config:
        populate_by_name = True


class TopTopic(BaseModel):
    topic: str
    count: int


class AnalyticsOut(BaseModel):
    location_id: Optional[int]
    total_reviews: int
    avg_rating: float
    response_rate: float  # percentage 0-100
    sentiment_distribution: SentimentDistribution
    rating_breakdown: Dict[str, int]
    top_topics: List[TopTopic]
    date_from: Optional[datetime]
    date_to: Optional[datetime]


# ---------------------------------------------------------------------------
# Trends schemas
# ---------------------------------------------------------------------------

class TrendPoint(BaseModel):
    period: str  # "2026-W18" or "2026-05"
    total_reviews: int
    avg_rating: float
    positive: int
    negative: int
    neutral: int
    mixed: int


class TrendsOut(BaseModel):
    location_id: Optional[int]
    group_by: str  # "week" | "month"
    data: List[TrendPoint]

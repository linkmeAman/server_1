"""Google Reviews tables: locations, reviews, analysis.

Revision ID: 20260507_008
Revises: 20260428_007
Create Date: 2026-05-07 00:00:08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260507_008"
down_revision = "20260428_007"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:
    if not _has_table("google_review_locations"):
        op.create_table(
            "google_review_locations",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("account_name", sa.String(length=255), nullable=False),
            sa.Column("location_name", sa.String(length=255), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=False),
            sa.Column("address", sa.Text(), nullable=True),
            sa.Column("place_id", sa.String(length=255), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
                server_onupdate=text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("location_name", name="uq_grl_location_name"),
        )

    if not _has_table("google_reviews"):
        op.create_table(
            "google_reviews",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("review_id", sa.String(length=512), nullable=False),
            sa.Column(
                "location_id",
                sa.BigInteger(),
                sa.ForeignKey("google_review_locations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("author_name", sa.String(length=255), nullable=True),
            sa.Column("author_photo_url", sa.Text(), nullable=True),
            sa.Column("rating", sa.SmallInteger(), nullable=False),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("review_time", sa.DateTime(), nullable=True),
            sa.Column("reply_text", sa.Text(), nullable=True),
            sa.Column("reply_time", sa.DateTime(), nullable=True),
            sa.Column("language", sa.String(length=10), nullable=True),
            sa.Column("raw_response", sa.JSON(), nullable=True),
            sa.Column(
                "synced_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("review_id", name="uq_google_reviews_review_id"),
            sa.Index("ix_google_reviews_location_id", "location_id"),
            sa.Index("ix_google_reviews_review_time", "review_time"),
            sa.Index("ix_google_reviews_rating", "rating"),
        )

    if not _has_table("review_analysis"):
        op.create_table(
            "review_analysis",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "review_id",
                sa.BigInteger(),
                sa.ForeignKey("google_reviews.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "sentiment",
                sa.Enum("positive", "negative", "neutral", "mixed", name="sentiment_label"),
                nullable=False,
            ),
            sa.Column("compound_score", sa.Float(), nullable=False),
            sa.Column("transformer_label", sa.String(length=32), nullable=True),
            sa.Column("transformer_score", sa.Float(), nullable=True),
            sa.Column("topics", sa.JSON(), nullable=True),
            sa.Column("keywords", sa.JSON(), nullable=True),
            sa.Column("model_version", sa.String(length=64), nullable=False, server_default="v1"),
            sa.Column(
                "analyzed_at",
                sa.DateTime(),
                nullable=False,
                server_default=text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("review_id", name="uq_review_analysis_review_id"),
            sa.Index("ix_review_analysis_sentiment", "sentiment"),
        )


def downgrade() -> None:
    if _has_table("review_analysis"):
        op.drop_table("review_analysis")
        # Drop enum type (MySQL ignores this but keep for Postgres compat)
        try:
            op.execute("DROP TYPE IF EXISTS sentiment_label")
        except Exception:
            pass
    if _has_table("google_reviews"):
        op.drop_table("google_reviews")
    if _has_table("google_review_locations"):
        op.drop_table("google_review_locations")

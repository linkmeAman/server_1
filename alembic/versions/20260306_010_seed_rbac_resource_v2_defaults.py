"""seed rbac_resource_v2 defaults

Revision ID: 20260306_010
Revises: 20260306_009
Create Date: 2026-03-06 00:00:10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260306_010"
down_revision = "20260306_009"
branch_labels = None
depends_on = None


RESOURCES = [
    ("global", "Global", None, 10),
    ("boards", "Boards", None, 20),
    ("boards.lead_board", "Lead Board", "boards", 10),
    ("reports", "Reports", None, 30),
    ("reports.top_summary", "Top Summary", "reports", 10),
    ("reports.source_breakdown", "Source Breakdown", "reports", 20),
    ("reports.center_performance", "Center Performance", "reports", 30),
    ("reports.funnel_stage_tracking", "Funnel Stage Tracking", "reports", 40),
    ("reports.campaign_performance", "Campaign Performance", "reports", 50),
    ("reports.heard_from_performance", "Heard From Performance", "reports", 60),
    ("reports.event_calendar", "Event Calendar", "reports", 70),
]


def upgrade() -> None:
    conn = op.get_bind()

    for code, name, _parent_code, sort_order in RESOURCES:
        conn.execute(
            sa.text(
                """
                INSERT INTO rbac_resource_v2 (code, name, parent_id, sort_order, meta, is_active, created_at, modified_at)
                VALUES (:code, :name, NULL, :sort_order, NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    sort_order = VALUES(sort_order),
                    is_active = 1,
                    modified_at = CURRENT_TIMESTAMP
                """
            ),
            {"code": code, "name": name, "sort_order": int(sort_order)},
        )

    for code, _name, parent_code, _sort_order in RESOURCES:
        if not parent_code:
            continue
        conn.execute(
            sa.text(
                """
                UPDATE rbac_resource_v2 child
                JOIN rbac_resource_v2 parent ON parent.code = :parent_code
                SET child.parent_id = parent.id,
                    child.modified_at = CURRENT_TIMESTAMP
                WHERE child.code = :code
                """
            ),
            {"code": code, "parent_code": parent_code},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            DELETE FROM rbac_resource_v2
            WHERE code IN (
                'global',
                'boards',
                'boards.lead_board',
                'reports',
                'reports.top_summary',
                'reports.source_breakdown',
                'reports.center_performance',
                'reports.funnel_stage_tracking',
                'reports.campaign_performance',
                'reports.heard_from_performance',
                'reports.event_calendar'
            )
            """
        )
    )

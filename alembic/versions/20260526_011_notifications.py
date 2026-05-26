"""Add notification event log and per-user state

Revision ID: 20260526_011
Revises: 20260520_010
Create Date: 2026-05-26

Notifications are universal application events.  The event table is immutable
delivery/debug history, while user state stores per-user read and cleared
flags without deleting the underlying log.
"""

from alembic import op

revision = "20260526_011"
down_revision = "20260520_010"
branch_labels = None
depends_on = None


def _main_db() -> str:
    from app.core.settings import settings

    return settings.DB_NAME


def upgrade() -> None:
    db = _main_db()
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS `{db}`.`notification_event` (
            `event_id` VARCHAR(36) NOT NULL,
            `request_id` VARCHAR(128) NOT NULL,
            `event_type` VARCHAR(128) NOT NULL,
            `severity` VARCHAR(20) NOT NULL,
            `source` VARCHAR(128) NOT NULL,
            `event_timestamp` VARCHAR(64) NOT NULL,
            `message` TEXT NOT NULL,
            `metadata_json` LONGTEXT NOT NULL,
            `user_id` VARCHAR(64) NULL,
            `group_key` VARCHAR(255) NULL,
            `dedupe_key` VARCHAR(255) NULL,
            `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            PRIMARY KEY (`event_id`),
            KEY `idx_notification_event_user_time` (`user_id`, `created_at`),
            KEY `idx_notification_event_request` (`request_id`),
            KEY `idx_notification_event_severity` (`severity`),
            KEY `idx_notification_event_source` (`source`),
            KEY `idx_notification_event_group` (`group_key`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS `{db}`.`notification_user_state` (
            `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            `event_id` VARCHAR(36) NOT NULL,
            `user_id` VARCHAR(64) NOT NULL,
            `read_at` DATETIME(6) NULL,
            `cleared_at` DATETIME(6) NULL,
            `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ON UPDATE CURRENT_TIMESTAMP(6),
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_notification_user_state_event_user` (`event_id`, `user_id`),
            KEY `idx_notification_user_state_user_read` (`user_id`, `read_at`),
            KEY `idx_notification_user_state_user_cleared` (`user_id`, `cleared_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS `{db}`.`notification_user_preference` (
            `user_id` VARCHAR(64) NOT NULL,
            `toast_enabled` TINYINT(1) NOT NULL DEFAULT 1,
            `desktop_enabled` TINYINT(1) NOT NULL DEFAULT 1,
            `silent_mode` TINYINT(1) NOT NULL DEFAULT 0,
            `minimum_toast_severity` VARCHAR(20) NOT NULL DEFAULT 'info',
            `minimum_desktop_severity` VARCHAR(20) NOT NULL DEFAULT 'info',
            `center_severity_filter` VARCHAR(20) NOT NULL DEFAULT 'all',
            `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ON UPDATE CURRENT_TIMESTAMP(6),
            PRIMARY KEY (`user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    db = _main_db()
    op.execute(f"DROP TABLE IF EXISTS `{db}`.`notification_user_preference`")
    op.execute(f"DROP TABLE IF EXISTS `{db}`.`notification_user_state`")
    op.execute(f"DROP TABLE IF EXISTS `{db}`.`notification_event`")

#!/usr/bin/env python3
"""Backfill auth_employee_user_map from legacy contact/employee/user data."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from app.core.database import central_session_context, main_session_context


@dataclass
class Stats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    conflicted: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill auth_employee_user_map")
    parser.add_argument("--dry-run", action="store_true", help="Print proposed writes only")
    parser.add_argument("--batch-size", type=int, default=500, help="Rows per batch")
    parser.add_argument("--sleep-ms", type=int, default=0, help="Delay between batches")
    return parser.parse_args()


async def _candidate_users(central_db, contact_id: int, user_account: Optional[int]) -> List[Dict]:
    # `user_account` is not used for candidate filtering; treat it as legacy metadata only.
    _ = user_account
    result = await central_db.execute(
        text(
            """
            SELECT id, contact_id, inactive
            FROM user
            WHERE contact_id = :contact_id
              AND inactive = 0
              AND (park IS NULL OR park = 0)
            """
        ),
        {"contact_id": int(contact_id)},
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def _existing_by_employee(central_db, employee_id: int) -> Optional[Dict]:
    result = await central_db.execute(
        text(
            """
            SELECT id, contact_id, employee_id, user_id, is_active
            FROM auth_employee_user_map
            WHERE employee_id = :employee_id
            LIMIT 1
            """
        ),
        {"employee_id": int(employee_id)},
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def _existing_by_user(central_db, user_id: int) -> Optional[Dict]:
    result = await central_db.execute(
        text(
            """
            SELECT id, contact_id, employee_id, user_id, is_active
            FROM auth_employee_user_map
            WHERE user_id = :user_id
            LIMIT 1
            """
        ),
        {"user_id": int(user_id)},
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


def _print_conflict(employee: Dict, users: List[Dict], reason: str) -> None:
    print(
        "CONFLICT",
        {
            "employee_id": int(employee["id"]),
            "contact_id": int(employee["contact_id"]),
            "user_account": employee.get("user_account"),
            "candidate_user_ids": [int(user["id"]) for user in users],
            "reason": reason,
        },
    )


async def _process_employee(
    *,
    employee: Dict,
    central_db,
    stats: Stats,
    dry_run: bool,
) -> None:
    employee_id = int(employee["id"])
    contact_id = int(employee["contact_id"])
    user_account = employee.get("user_account")
    if employee.get("contact_exists") is None:
        stats.skipped += 1
        print("SKIP", {"employee_id": employee_id, "reason": "missing_contact"})
        return

    candidates = await _candidate_users(central_db, contact_id, user_account)
    if not candidates:
        stats.skipped += 1
        print("SKIP", {"employee_id": employee_id, "reason": "no_user_candidate"})
        return

    if len(candidates) != 1:
        stats.conflicted += 1
        _print_conflict(employee, candidates, "multiple_users_for_employee")
        return

    user = candidates[0]
    user_id = int(user["id"])

    existing_employee = await _existing_by_employee(central_db, employee_id)
    existing_user = await _existing_by_user(central_db, user_id)

    if existing_employee and int(existing_employee["user_id"]) != user_id:
        stats.conflicted += 1
        _print_conflict(employee, candidates, "employee_already_mapped_to_different_user")
        return

    if existing_user and int(existing_user["employee_id"]) != employee_id:
        stats.conflicted += 1
        _print_conflict(employee, candidates, "user_already_mapped_to_different_employee")
        return

    now = datetime.utcnow()
    is_create = existing_employee is None

    if dry_run:
        action = "CREATE" if is_create else "UPDATE"
        print(
            action,
            {
                "employee_id": employee_id,
                "contact_id": contact_id,
                "user_id": user_id,
                "is_active": 1,
            },
        )
        if is_create:
            stats.created += 1
        else:
            stats.updated += 1
        return

    await central_db.execute(
        text(
            """
            INSERT INTO auth_employee_user_map (
                contact_id, employee_id, user_id, is_active,
                created_at, modified_at, created_by_user_id, modified_by_user_id, source
            ) VALUES (
                :contact_id, :employee_id, :user_id, :is_active,
                :created_at, :modified_at, NULL, NULL, :source
            )
            ON DUPLICATE KEY UPDATE
                contact_id = VALUES(contact_id),
                user_id = VALUES(user_id),
                is_active = VALUES(is_active),
                modified_at = VALUES(modified_at),
                modified_by_user_id = VALUES(modified_by_user_id),
                source = VALUES(source)
            """
        ),
        {
            "contact_id": contact_id,
            "employee_id": employee_id,
            "user_id": user_id,
            "is_active": 1,
            "created_at": now,
            "modified_at": now,
            "source": "backfill_script",
        },
    )

    if is_create:
        stats.created += 1
    else:
        stats.updated += 1


async def run(dry_run: bool, batch_size: int, sleep_ms: int) -> Stats:
    stats = Stats()
    offset = 0

    async with main_session_context() as main_db, central_session_context() as central_db:
        while True:
            result = await main_db.execute(
                text(
                    """
                    SELECT e.id, e.contact_id, e.user_account, c.id AS contact_exists
                    FROM employee e
                    LEFT JOIN contact c ON c.id = e.contact_id
                    WHERE e.contact_id IS NOT NULL
                      AND (e.park IS NULL OR e.park = 0)
                      AND (c.park IS NULL OR c.park = 0)
                    ORDER BY e.id ASC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"limit": int(batch_size), "offset": int(offset)},
            )
            employees = [dict(row._mapping) for row in result.fetchall()]
            if not employees:
                break

            for employee in employees:
                try:
                    await _process_employee(
                        employee=employee,
                        central_db=central_db,
                        stats=stats,
                        dry_run=dry_run,
                    )
                except Exception as exc:
                    stats.conflicted += 1
                    print(
                        "CONFLICT",
                        {
                            "employee_id": int(employee["id"]),
                            "reason": "exception",
                            "details": str(exc),
                        },
                    )

            if not dry_run:
                await central_db.commit()

            offset += len(employees)
            if sleep_ms > 0:
                await asyncio.sleep(sleep_ms / 1000.0)

    return stats


def _print_summary(stats: Stats) -> None:
    print("SUMMARY")
    print(f"created={stats.created}")
    print(f"updated={stats.updated}")
    print(f"skipped={stats.skipped}")
    print(f"conflicted={stats.conflicted}")


async def amain() -> int:
    args = parse_args()
    stats = await run(
        dry_run=bool(args.dry_run),
        batch_size=max(1, int(args.batch_size)),
        sleep_ms=max(0, int(args.sleep_ms)),
    )
    _print_summary(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))


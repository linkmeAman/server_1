#!/usr/bin/env python3
"""Backfill bcrypt hashes into `user.password_hash` from legacy `user.password`."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from core.database import central_session_context
from core.security import hash_password


@dataclass
class Stats:
    scanned: int = 0
    updated: int = 0
    skipped_no_password: int = 0
    skipped_has_hash: int = 0
    failed: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill user.password_hash from user.password",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write updates")
    parser.add_argument("--batch-size", type=int, default=500, help="Rows per batch")
    parser.add_argument("--limit", type=int, default=0, help="Stop after processing this many rows total (0 = no limit)")
    parser.add_argument("--sleep-ms", type=int, default=0, help="Delay between batches")
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive users (default: only inactive=0)",
    )
    return parser.parse_args()


async def _fetch_batch(central_db, *, offset: int, limit: int, include_inactive: bool):
    where_clauses = ["(park IS NULL OR park = 0)"]
    if not include_inactive:
        where_clauses.append("inactive = 0")

    where_sql = " AND ".join(where_clauses)
    result = await central_db.execute(
        text(
            f"""
            SELECT id, password, password_hash
            FROM user
            WHERE {where_sql}
            ORDER BY id ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": int(limit), "offset": int(offset)},
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def run(*, dry_run: bool, batch_size: int, limit: int, sleep_ms: int, include_inactive: bool) -> Stats:
    stats = Stats()
    offset = 0

    async with central_session_context() as central_db:
        while True:
            fetch_size = batch_size
            if limit > 0:
                remaining = limit - stats.scanned
                if remaining <= 0:
                    break
                fetch_size = min(batch_size, remaining)

            rows = await _fetch_batch(
                central_db,
                offset=offset,
                limit=fetch_size,
                include_inactive=include_inactive,
            )
            if not rows:
                break

            for row in rows:
                stats.scanned += 1
                user_id = int(row["id"])
                legacy_password = str(row.get("password") or "")
                existing_hash = str(row.get("password_hash") or "").strip()

                if existing_hash:
                    stats.skipped_has_hash += 1
                    continue

                if not legacy_password:
                    stats.skipped_no_password += 1
                    continue

                try:
                    hashed = hash_password(legacy_password)
                    if dry_run:
                        stats.updated += 1
                        print("DRYRUN", {"user_id": user_id, "action": "set_password_hash"})
                        continue

                    await central_db.execute(
                        text(
                            """
                            UPDATE user
                            SET password_hash = :password_hash,
                                password_hash_algo = :password_hash_algo,
                                password_hash_updated_at = :password_hash_updated_at
                            WHERE id = :user_id
                            """
                        ),
                        {
                            "user_id": user_id,
                            "password_hash": hashed,
                            "password_hash_algo": "bcrypt",
                            "password_hash_updated_at": datetime.utcnow(),
                        },
                    )
                    stats.updated += 1
                except Exception as exc:
                    stats.failed += 1
                    print("FAILED", {"user_id": user_id, "error": str(exc)})

            if not dry_run:
                await central_db.commit()

            offset += len(rows)
            if sleep_ms > 0:
                await asyncio.sleep(sleep_ms / 1000.0)

    return stats


def print_summary(stats: Stats) -> None:
    print("SUMMARY")
    print(f"scanned={stats.scanned}")
    print(f"updated={stats.updated}")
    print(f"skipped_has_hash={stats.skipped_has_hash}")
    print(f"skipped_no_password={stats.skipped_no_password}")
    print(f"failed={stats.failed}")


async def amain() -> int:
    args = parse_args()
    stats = await run(
        dry_run=bool(args.dry_run),
        batch_size=max(1, int(args.batch_size)),
        limit=max(0, int(args.limit)),
        sleep_ms=max(0, int(args.sleep_ms)),
        include_inactive=bool(args.include_inactive),
    )
    print_summary(stats)
    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))


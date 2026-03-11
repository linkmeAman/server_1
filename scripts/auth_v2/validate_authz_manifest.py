#!/usr/bin/env python3
"""Validate authz resource manifest and optional DB drift."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RESOURCE_CODE_RE = re.compile(r"^[a-z0-9_.-]+$")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate authz resources manifest")
    parser.add_argument(
        "--manifest",
        default="authz/resources_manifest.yaml",
        help="Path to resources manifest YAML file",
    )
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Also compare manifest resources with rbac_resource_v2 in central DB",
    )
    return parser.parse_args()


def _validate_code(code: str) -> bool:
    if not code:
        return False
    if not RESOURCE_CODE_RE.match(code):
        return False
    if code.startswith(".") or code.endswith("."):
        return False
    if ".." in code:
        return False
    return True


def _load_manifest(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    resources = payload.get("resources")
    if not isinstance(resources, list):
        raise RuntimeError("Manifest must include a top-level 'resources' list")
    normalized: List[Dict[str, Any]] = []
    for item in resources:
        if not isinstance(item, dict):
            raise RuntimeError("Each manifest resource entry must be an object")
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        parent_value = item.get("parent")
        parent = str(parent_value).strip() if parent_value is not None else None
        sort_order = int(item.get("sort_order") or 0)
        normalized.append(
            {
                "code": code,
                "name": name,
                "parent": parent if parent else None,
                "sort_order": sort_order,
            }
        )
    return normalized


def _validate_manifest_records(records: List[Dict[str, Any]]) -> List[str]:
    errors: List[str] = []
    seen_codes: set[str] = set()

    for record in records:
        code = record["code"]
        name = record["name"]
        if not code:
            errors.append("resource code is required")
            continue
        if not _validate_code(code):
            errors.append(f"invalid resource code: {code}")
        if code in seen_codes:
            errors.append(f"duplicate resource code: {code}")
        seen_codes.add(code)
        if not name:
            errors.append(f"name is required for code: {code}")

    for record in records:
        parent = record.get("parent")
        code = record["code"]
        if parent and parent not in seen_codes:
            errors.append(f"parent not found for code {code}: {parent}")
        if parent and parent == code:
            errors.append(f"resource parent cannot reference itself: {code}")

    return errors


async def _fetch_db_catalog() -> Dict[str, Dict[str, Any]]:
    from core.database_v2 import central_session_context

    async with central_session_context() as central_db:
        result = await central_db.execute(
            text(
                """
                SELECT child.code AS code, parent.code AS parent_code, child.name AS name, child.sort_order AS sort_order
                FROM rbac_resource_v2 child
                LEFT JOIN rbac_resource_v2 parent ON parent.id = child.parent_id
                WHERE child.is_active = 1
                """
            )
        )
        catalog: Dict[str, Dict[str, Any]] = {}
        for row in result.fetchall():
            row_map = row._mapping
            code = str(row_map.get("code") or "")
            if not code:
                continue
            catalog[code] = {
                "parent": str(row_map.get("parent_code")) if row_map.get("parent_code") is not None else None,
                "name": str(row_map.get("name") or ""),
                "sort_order": int(row_map.get("sort_order") or 0),
            }
        return catalog


def _compare_manifest_with_db(
    records: List[Dict[str, Any]],
    db_catalog: Dict[str, Dict[str, Any]],
) -> Tuple[List[str], Dict[str, Any]]:
    manifest_catalog = {row["code"]: row for row in records}
    errors: List[str] = []

    manifest_codes = set(manifest_catalog.keys())
    db_codes = set(db_catalog.keys())
    missing_in_db = sorted(manifest_codes - db_codes)
    extra_in_db = sorted(db_codes - manifest_codes)

    mismatches: List[Dict[str, Any]] = []
    for code in sorted(manifest_codes & db_codes):
        manifest_row = manifest_catalog[code]
        db_row = db_catalog[code]
        mismatch_fields = {}
        if manifest_row.get("parent") != db_row.get("parent"):
            mismatch_fields["parent"] = {
                "manifest": manifest_row.get("parent"),
                "db": db_row.get("parent"),
            }
        if manifest_row.get("name") != db_row.get("name"):
            mismatch_fields["name"] = {
                "manifest": manifest_row.get("name"),
                "db": db_row.get("name"),
            }
        if int(manifest_row.get("sort_order") or 0) != int(db_row.get("sort_order") or 0):
            mismatch_fields["sort_order"] = {
                "manifest": int(manifest_row.get("sort_order") or 0),
                "db": int(db_row.get("sort_order") or 0),
            }
        if mismatch_fields:
            mismatches.append({"code": code, "fields": mismatch_fields})

    if missing_in_db:
        errors.append(f"manifest codes missing in DB: {', '.join(missing_in_db)}")
    if extra_in_db:
        errors.append(f"DB has extra active codes not in manifest: {', '.join(extra_in_db)}")
    if mismatches:
        errors.append("manifest/db metadata drift detected")

    report = {
        "missing_in_db": missing_in_db,
        "extra_in_db": extra_in_db,
        "mismatches": mismatches,
    }
    return errors, report


async def _run_async(args: argparse.Namespace) -> int:
    records = _load_manifest(args.manifest)
    errors = _validate_manifest_records(records)
    report: Dict[str, Any] = {"manifest_count": len(records)}

    if args.check_db:
        db_catalog = await _fetch_db_catalog()
        compare_errors, compare_report = _compare_manifest_with_db(records, db_catalog)
        errors.extend(compare_errors)
        report["db_count"] = len(db_catalog)
        report["db_drift"] = compare_report

    if errors:
        print("AUTHZ_MANIFEST_VALIDATION_FAILED")
        for error in errors:
            print(f"- {error}")
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 1

    print("AUTHZ_MANIFEST_VALIDATION_OK")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_run_async(args))
    except Exception as exc:
        print("AUTHZ_MANIFEST_VALIDATION_FAILED")
        print(f"- {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

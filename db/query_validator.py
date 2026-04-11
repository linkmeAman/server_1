"""Validation helpers for read-only SQL execution."""

from __future__ import annotations

import os
import re

import sqlparse


class QueryValidationError(ValueError):
    """Raised when an incoming SQL query violates explorer safety rules."""


MAX_ROWS = int(os.getenv('MAX_QUERY_ROWS', '1000'))
BLOCKED_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "grant",
    "revoke",
)


def validate_query(query: str) -> str:
    cleaned = (query or "").strip()
    if not cleaned:
        raise QueryValidationError("Query is required")

    if ";" in cleaned:
        raise QueryValidationError("Semicolons are not allowed")

    lowered = cleaned.lower()
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            raise QueryValidationError(f"Blocked SQL keyword detected: {keyword}")

    statements = sqlparse.parse(cleaned)
    if len(statements) != 1:
        raise QueryValidationError("Only a single SQL statement is allowed")

    statement = statements[0]
    if statement.get_type() != "SELECT":
        raise QueryValidationError("Only SELECT queries are allowed")

    if not re.match(r"^\s*select\b", lowered):
        raise QueryValidationError("Query must start with SELECT")

    return cleaned


def apply_row_limit(query: str, max_rows: int = MAX_ROWS) -> str:
    """Append or validate LIMIT so responses stay bounded."""
    limit_match = re.search(r"\blimit\s+(\d+)\b", query, re.IGNORECASE)
    if not limit_match:
        return f"{query} LIMIT {max_rows}"

    requested = int(limit_match.group(1))
    if requested > max_rows:
        raise QueryValidationError(
            f"Requested LIMIT exceeds maximum allowed rows ({max_rows})"
        )
    return query

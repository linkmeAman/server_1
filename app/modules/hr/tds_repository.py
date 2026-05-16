"""TDS certificate repository — raw SQL queries against the main DB."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Filename parser
# ---------------------------------------------------------------------------

# Expected format: FIRSTNAME LASTNAME_Q{N}_FY{YYYYMM}_16A.pdf
# or variations.  We parse loosely.
_FY_RE = re.compile(r"FY(\d{4})(\d{2})", re.IGNORECASE)
_QUARTER_RE = re.compile(r"Q(\d)", re.IGNORECASE)
_EXT_RE = re.compile(r"\.[^.]+$")


def parse_tds_filename(filename: str) -> dict[str, Any]:
    """Extract name, quarter, and fiscal_year from a TDS certificate filename.

    Expected format (loose):
        ``VIDHI JIGNESH PARMAR_Q2_FY202526_16A.pdf``

    Returns dict with keys: ``parsed_name``, ``quarter`` (int|None),
    ``fiscal_year`` (str|None).
    """
    stem = _EXT_RE.sub("", filename).strip()

    quarter: int | None = None
    fiscal_year: str | None = None
    parsed_name: str | None = None

    # Quarter
    qm = _QUARTER_RE.search(stem)
    if qm:
        quarter = int(qm.group(1))

    # Fiscal year — FY202526 → "2025-26"
    fym = _FY_RE.search(stem)
    if fym:
        start_year = fym.group(1)
        end_suffix = fym.group(2)
        fiscal_year = f"{start_year}-{end_suffix}"

    # Name: everything before the first underscore
    parts = stem.split("_")
    if parts:
        parsed_name = parts[0].strip().upper()

    return {
        "parsed_name": parsed_name,
        "quarter": quarter,
        "fiscal_year": fiscal_year,
    }


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class TDSRepository:
    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    async def create_batch(
        self,
        db: AsyncSession,
        *,
        upload_type: str,
        original_filename: str,
        uploaded_by: int | None,
    ) -> int:
        result = await db.execute(
            text(
                """
                INSERT INTO tds_upload_batch (upload_type, original_filename, uploaded_by, status)
                VALUES (:upload_type, :original_filename, :uploaded_by, 'pending')
                """
            ),
            {
                "upload_type": upload_type,
                "original_filename": original_filename,
                "uploaded_by": uploaded_by,
            },
        )
        await db.commit()
        return result.lastrowid  # type: ignore[return-value]

    async def update_batch_file_count(
        self,
        db: AsyncSession,
        batch_id: int,
        total_files: int,
        status: str = "uploaded",
    ) -> None:
        await db.execute(
            text(
                """
                UPDATE tds_upload_batch
                SET total_files = :total_files, status = :status
                WHERE id = :id
                """
            ),
            {"total_files": total_files, "status": status, "id": batch_id},
        )
        await db.commit()

    async def get_batch(self, db: AsyncSession, batch_id: int) -> dict[str, Any] | None:
        result = await db.execute(
            text("SELECT * FROM tds_upload_batch WHERE id = :id"),
            {"id": batch_id},
        )
        row = result.fetchone()
        return dict(row._mapping) if row else None

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    async def insert_document(
        self,
        db: AsyncSession,
        *,
        batch_id: int,
        s3_key: str,
        original_filename: str,
        parsed_name: str | None,
        quarter: int | None,
        fiscal_year: str | None,
    ) -> int:
        result = await db.execute(
            text(
                """
                INSERT INTO tds_document
                    (batch_id, s3_key, original_filename, parsed_name, quarter, fiscal_year, mapping_status)
                VALUES
                    (:batch_id, :s3_key, :original_filename, :parsed_name, :quarter, :fiscal_year, 'unmapped')
                """
            ),
            {
                "batch_id": batch_id,
                "s3_key": s3_key,
                "original_filename": original_filename,
                "parsed_name": parsed_name,
                "quarter": quarter,
                "fiscal_year": fiscal_year,
            },
        )
        return result.lastrowid  # type: ignore[return-value]

    async def find_existing_documents(
        self,
        db: AsyncSession,
        filenames: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Return DB info for the most recent row matching each filename.

        Returns a dict mapping ``original_filename`` →
        ``{id, s3_key, employee_id, mapping_status}``.

        Used to decide per-file upload behaviour:
        - filename absent            → truly new, upload to S3
        - present, employee_id NULL  → unmapped/failed, rebatch without re-uploading
        - present, employee_id set   → already mapped, skip entirely
        """
        if not filenames:
            return {}
        placeholders = ", ".join(f":fn_{i}" for i in range(len(filenames)))
        params: dict[str, Any] = {f"fn_{i}": name for i, name in enumerate(filenames)}
        result = await db.execute(
            text(
                f"""
                SELECT d.id, d.original_filename, d.s3_key,
                       d.employee_id, d.mapping_status
                FROM tds_document d
                INNER JOIN (
                    SELECT MAX(id) AS max_id
                    FROM tds_document
                    WHERE original_filename IN ({placeholders})
                    GROUP BY original_filename
                ) latest ON d.id = latest.max_id
                """
            ),
            params,
        )
        return {
            row[1]: {
                "id": row[0],
                "s3_key": row[2],
                "employee_id": row[3],
                "mapping_status": row[4],
            }
            for row in result.fetchall()
        }

    async def rebatch_documents(
        self,
        db: AsyncSession,
        doc_ids: list[int],
        new_batch_id: int,
    ) -> None:
        """Re-assign existing unmapped document rows to *new_batch_id*.

        Resets ``mapping_status`` back to ``unmapped`` so the auto-mapper
        gets a fresh pass on them.  The S3 object is untouched — the
        existing ``s3_key`` is simply reused in the new batch.
        """
        if not doc_ids:
            return
        placeholders = ", ".join(f":id_{i}" for i in range(len(doc_ids)))
        params: dict[str, Any] = {f"id_{i}": did for i, did in enumerate(doc_ids)}
        params["new_batch_id"] = new_batch_id
        await db.execute(
            text(
                f"""
                UPDATE tds_document
                SET batch_id = :new_batch_id,
                    mapping_status = 'unmapped'
                WHERE id IN ({placeholders})
                """
            ),
            params,
        )
        await db.commit()

    async def bulk_insert_documents(
        self,
        db: AsyncSession,
        batch_id: int,
        records: list[dict[str, Any]],
    ) -> None:
        """Insert multiple document rows in a single transaction."""
        for rec in records:
            parsed = parse_tds_filename(rec["original_filename"])
            await db.execute(
                text(
                    """
                    INSERT INTO tds_document
                        (batch_id, s3_key, original_filename, parsed_name, quarter, fiscal_year, mapping_status)
                    VALUES
                        (:batch_id, :s3_key, :original_filename, :parsed_name, :quarter, :fiscal_year, 'unmapped')
                    """
                ),
                {
                    "batch_id": batch_id,
                    "s3_key": rec["s3_key"],
                    "original_filename": rec["original_filename"],
                    "parsed_name": parsed["parsed_name"],
                    "quarter": parsed["quarter"],
                    "fiscal_year": parsed["fiscal_year"],
                },
            )
        await db.commit()

    async def list_documents_for_batch(
        self, db: AsyncSession, batch_id: int
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            text("SELECT * FROM tds_document WHERE batch_id = :batch_id ORDER BY id"),
            {"batch_id": batch_id},
        )
        return [dict(r._mapping) for r in result.fetchall()]

    async def apply_mapping(
        self,
        db: AsyncSession,
        mappings: list[dict[str, Any]],
    ) -> None:
        """Persist employee→document mappings.

        Each item in *mappings* must have:
            ``doc_id``, ``employee_id`` (int|None), ``mapping_status``
        """
        for m in mappings:
            await db.execute(
                text(
                    """
                    UPDATE tds_document
                    SET employee_id = :employee_id,
                        mapping_status = :mapping_status
                    WHERE id = :doc_id
                    """
                ),
                {
                    "doc_id": m["doc_id"],
                    "employee_id": m.get("employee_id"),
                    "mapping_status": m.get("mapping_status", "manual_mapped"),
                },
            )
        await db.commit()

    async def mark_batch_mapped(self, db: AsyncSession, batch_id: int) -> None:
        await db.execute(
            text(
                "UPDATE tds_upload_batch SET status = 'mapped' WHERE id = :id"
            ),
            {"id": batch_id},
        )
        await db.commit()

    # ------------------------------------------------------------------
    # View queries
    # ------------------------------------------------------------------

    async def list_employees_with_tds(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        status: int | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Return employee rows that have at least one TDS document, plus all
        employees (to show even those without documents)."""
        filters = ["1=1"]
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if q:
            filters.append(
                "(c.fname LIKE :q OR c.lname LIKE :q OR c.mname LIKE :q OR e.ecode LIKE :q)"
            )
            params["q"] = f"%{q}%"
        if status is not None:
            filters.append("e.status = :status")
            params["status"] = status

        where = " AND ".join(filters)
        result = await db.execute(
            text(
                f"""
                SELECT
                    e.id            AS employee_id,
                    c.fname,
                    c.mname,
                    c.lname,
                    CONCAT_WS(' ',
                        NULLIF(TRIM(c.fname),''),
                        NULLIF(TRIM(c.mname),''),
                        NULLIF(TRIM(c.lname),'')
                    )               AS full_name,
                    e.ecode,
                    e.status,
                    e.department_id,
                    e.position_id
                FROM employee e
                LEFT JOIN contact c ON c.id = e.contact_id
                WHERE {where}
                  AND (e.park IS NULL OR e.park = 0)
                ORDER BY full_name ASC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        return [dict(r._mapping) for r in result.fetchall()]

    async def count_employees(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        status: int | None,
    ) -> int:
        filters = ["1=1"]
        params: dict[str, Any] = {}
        if q:
            filters.append(
                "(c.fname LIKE :q OR c.lname LIKE :q OR c.mname LIKE :q OR e.ecode LIKE :q)"
            )
            params["q"] = f"%{q}%"
        if status is not None:
            filters.append("e.status = :status")
            params["status"] = status
        where = " AND ".join(filters)
        result = await db.execute(
            text(
                f"""
                SELECT COUNT(*) AS cnt
                FROM employee e
                LEFT JOIN contact c ON c.id = e.contact_id
                WHERE {where}
                  AND (e.park IS NULL OR e.park = 0)
                """
            ),
            params,
        )
        row = result.fetchone()
        return int(row.cnt) if row else 0

    async def get_tds_years_for_employee(
        self, db: AsyncSession, employee_id: int
    ) -> list[str]:
        result = await db.execute(
            text(
                """
                SELECT DISTINCT fiscal_year
                FROM tds_document
                WHERE employee_id = :eid AND fiscal_year IS NOT NULL
                ORDER BY fiscal_year DESC
                """
            ),
            {"eid": employee_id},
        )
        return [r.fiscal_year for r in result.fetchall()]

    async def get_tds_docs_for_employee_year(
        self, db: AsyncSession, employee_id: int, fiscal_year: str
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT id, s3_key, original_filename, quarter, fiscal_year, mapping_status
                FROM tds_document
                WHERE employee_id = :eid AND fiscal_year = :fy
                ORDER BY quarter ASC
                """
            ),
            {"eid": employee_id, "fy": fiscal_year},
        )
        return [dict(r._mapping) for r in result.fetchall()]

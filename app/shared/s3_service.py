"""Optimised S3 service for file upload, download, and signed-URL generation.

All AWS credentials are pulled from ``app.core.settings`` so the service
works with environment variables, IAM instance profiles, or SSM-backed
settings — no hard-coded credentials anywhere.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import PurePosixPath
from typing import IO, Any

from app.core.settings import get_settings

logger = logging.getLogger(__name__)


def _get_s3_client() -> Any:
    """Return a boto3 S3 client, lazily imported so the module loads in
    environments without boto3 (pure unit-test / no-AWS setups)."""
    import boto3  # noqa: PLC0415

    settings = get_settings()
    kwargs: dict[str, Any] = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("s3", **kwargs)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def generate_presigned_get_url(s3_key: str, expiry: int | None = None) -> str:
    """Return a pre-signed GET URL for *s3_key*.

    Args:
        s3_key: Full S3 object key (e.g. ``hr/tds/2025/file.pdf``).
        expiry: URL validity in seconds.  Defaults to ``S3_PRESIGN_EXPIRY_SECONDS``.

    Returns:
        Pre-signed HTTPS URL string.
    """
    settings = get_settings()
    client = _get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": s3_key},
        ExpiresIn=expiry if expiry is not None else settings.S3_PRESIGN_EXPIRY_SECONDS,
    )


def upload_fileobj(
    fileobj: IO[bytes],
    s3_key: str,
    content_type: str = "application/octet-stream",
    extra_args: dict[str, Any] | None = None,
) -> str:
    """Upload a file-like object to S3.

    Args:
        fileobj: Readable binary file-like object.
        s3_key: Destination key in the configured bucket.
        content_type: MIME type stored as ``ContentType`` metadata.
        extra_args: Optional extra arguments forwarded to ``put_object``
            (e.g. ``{"ServerSideEncryption": "AES256"}``).

    Returns:
        The *s3_key* that was written.
    """
    settings = get_settings()
    client = _get_s3_client()
    put_args: dict[str, Any] = {"ContentType": content_type}
    if extra_args:
        put_args.update(extra_args)
    client.upload_fileobj(
        fileobj,
        settings.S3_BUCKET,
        s3_key,
        ExtraArgs=put_args,
    )
    return s3_key


def delete_object(s3_key: str) -> None:
    """Delete a single object from S3."""
    settings = get_settings()
    client = _get_s3_client()
    client.delete_object(Bucket=settings.S3_BUCKET, Key=s3_key)


# ---------------------------------------------------------------------------
# ZIP extraction helper
# ---------------------------------------------------------------------------


class ExtractedFile:
    """Lightweight container for a single file extracted from a ZIP."""

    __slots__ = ("filename", "data", "content_type")

    def __init__(self, filename: str, data: bytes, content_type: str) -> None:
        self.filename = filename
        self.data = data
        self.content_type = content_type


def _open_zip(zip_bytes: bytes) -> zipfile.ZipFile:
    """Open a ZipFile from raw bytes, handling ZIPs with prepended data.

    Some tools (macOS Archive Utility, Windows built-in compression, certain
    self-extracting stubs) prepend extra bytes before the first local file
    header, causing ``zipfile.ZipFile`` to raise
    ``BadZipFile: Bad offset for central directory`` even though
    ``is_zipfile`` returns True (it only checks for the EOCD signature).

    The fix: if the initial open fails with that error, scan for the local
    file header signature ``PK\\x03\\x04`` and retry with the corrected slice.
    """
    try:
        return zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        # Attempt offset correction: find the first local file header.
        offset = zip_bytes.find(b"PK\x03\x04")
        if offset > 0:
            logger.debug(
                "ZIP has %d prepended bytes; retrying after offset correction.", offset
            )
            try:
                return zipfile.ZipFile(io.BytesIO(zip_bytes[offset:]))
            except zipfile.BadZipFile:
                pass  # fall through to the original error
        raise ValueError(
            "ZIP file is corrupted or uses an unsupported format. "
            "Please re-create the archive using standard ZIP compression."
        ) from exc


def extract_pdfs_from_zip(zip_bytes: bytes) -> list[ExtractedFile]:
    """Extract every PDF from *zip_bytes*, ignoring directory structure.

    The function walks all entries in the ZIP archive — including nested
    sub-folders — and returns only the files that end with ``.pdf`` (case-
    insensitive).  Directory hierarchy inside the ZIP is intentionally
    discarded; only the bare filename is preserved.

    Args:
        zip_bytes: Raw bytes of the ZIP archive.

    Returns:
        List of :class:`ExtractedFile` objects, one per PDF found.

    Raises:
        ValueError: If *zip_bytes* is not a valid ZIP file.
    """
    if not zipfile.is_zipfile(io.BytesIO(zip_bytes)):
        raise ValueError("Provided bytes are not a valid ZIP archive.")

    results: list[ExtractedFile] = []
    with _open_zip(zip_bytes) as zf:
        for info in zf.infolist():
            # Skip directory entries
            if info.is_dir():
                continue
            # Only PDF files
            if not info.filename.lower().endswith(".pdf"):
                continue
            # Flatten: take only the bare filename, ignore sub-folder paths
            bare_name = PurePosixPath(info.filename).name
            if not bare_name:
                continue
            data = zf.read(info.filename)
            results.append(ExtractedFile(bare_name, data, "application/pdf"))

    return results


def upload_pdfs_to_s3(
    files: list[ExtractedFile],
    folder_prefix: str,
) -> list[dict[str, str]]:
    """Upload a list of :class:`ExtractedFile` objects to S3 under *folder_prefix*.

    Each file is stored at ``<folder_prefix>/<filename>``.  If two files share
    the same name the second one is deduplicated with a numeric suffix before
    the extension.

    Args:
        files: List of files to upload.
        folder_prefix: S3 key prefix (no trailing slash).

    Returns:
        List of dicts ``{"original_filename": str, "s3_key": str}``.
    """
    seen: dict[str, int] = {}
    results: list[dict[str, str]] = []

    for ef in files:
        name = ef.filename
        # Deduplicate names within the batch
        if name in seen:
            seen[name] += 1
            stem, _, ext = name.rpartition(".")
            name = f"{stem}_{seen[name]}.{ext}"
        else:
            seen[name] = 0

        s3_key = f"{folder_prefix}/{name}"
        upload_fileobj(io.BytesIO(ef.data), s3_key, ef.content_type)
        results.append({"original_filename": ef.filename, "s3_key": s3_key})

    return results

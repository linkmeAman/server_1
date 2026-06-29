from __future__ import annotations

import re
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from typing import Iterable
from uuid import uuid4

from app.modules.documents.schemas import DocumentPreviewJob, DocumentPreviewPage, DocumentSection, DocumentSnapshot

_BLOCK_TAGS = {"p", "div", "blockquote", "pre", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
_ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strong",
    "u",
    "ul",
}
_VOID_TAGS = {"br", "img"}
_MAX_PAGE_UNITS = 900


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._parts.append("\n")
        elif tag == "li":
            self._parts.append("- ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS or tag in {"ul", "ol"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = text.replace("\xa0", " ")
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        return text.strip()


class PreviewService:
    def render(self, snapshot: DocumentSnapshot) -> DocumentPreviewJob:
        pages: list[DocumentPreviewPage] = []
        page_number = 1

        for section in snapshot.sections:
            section_pages = self._render_section(section, page_number)
            pages.extend(section_pages)
            page_number += len(section_pages)

        if not pages:
            pages.append(
                DocumentPreviewPage(
                    page_number=1,
                    section_id="cover",
                    title=snapshot.title,
                    html=self._wrap_page_html(snapshot.title, snapshot.title, "<p></p>", snapshot),
                )
            )

        preview_html = "".join(page.html for page in pages)
        now = datetime.now(timezone.utc)
        return DocumentPreviewJob(
            id=str(uuid4()),
            document_id=None,
            document_version_id=None,
            status="completed",
            page_count=len(pages),
            preview_html=preview_html,
            pages=pages,
            created_at=now,
            completed_at=now,
        )

    def _render_section(self, section: DocumentSection, start_page_number: int) -> list[DocumentPreviewPage]:
        block_html = self._collect_block_html(section.content)
        if not block_html:
            block_html = ["<p></p>"]

        pages: list[DocumentPreviewPage] = []
        current_blocks: list[str] = []
        current_units = 0
        page_number = start_page_number

        for block in block_html:
            block_units = self._estimate_block_units(block)
            if current_blocks and current_units + block_units > _MAX_PAGE_UNITS:
                pages.append(self._build_page(page_number, section, current_blocks))
                page_number += 1
                current_blocks = []
                current_units = 0

            current_blocks.append(block)
            current_units += block_units

        if current_blocks or not pages:
            pages.append(self._build_page(page_number, section, current_blocks or ["<p></p>"]))

        return pages

    def _build_page(self, page_number: int, section: DocumentSection, paragraphs: Iterable[str]) -> DocumentPreviewPage:
        body_html = "".join(paragraphs)
        return DocumentPreviewPage(
            page_number=page_number,
            section_id=section.id,
            title=section.title,
            html=self._wrap_page_html(section.title, section.title, body_html, None),
        )

    def _wrap_page_html(
        self,
        title: str,
        section_title: str,
        body_html: str,
        snapshot: DocumentSnapshot | None,
    ) -> str:
        page_size = snapshot.layout.page_size if snapshot and hasattr(snapshot, "layout") else None
        orientation = snapshot.layout.orientation if snapshot and hasattr(snapshot, "layout") else None
        meta_parts = [part for part in [page_size, orientation] if part]
        meta_html = f'<div class="doc-preview-meta">{escape(" | ".join(meta_parts))}</div>' if meta_parts else ""
        return (
            '<article class="doc-preview-page">'
            f'<header class="doc-preview-header"><div class="doc-preview-title">{escape(title)}</div>{meta_html}</header>'
            f'<section class="doc-preview-section"><h2>{escape(section_title)}</h2>{body_html}</section>'
            '</article>'
        )

    def _collect_block_html(self, content: str) -> list[str]:
        normalized = self._normalize_content_html(content)
        matches = list(
            re.finditer(
                r"<(p|blockquote|pre|h1|h2|h3|h4|h5|h6|ul|ol)(?:\s+[^>]*)?>.*?</\1>",
                normalized,
                re.IGNORECASE | re.DOTALL,
            )
        )
        if matches:
            return [self._sanitize_html(match.group(0)) for match in matches]
        return [f"<p>{escape(self._extract_plain_text(normalized)).replace(chr(10), '<br/>')}</p>"]

    def _normalize_content_html(self, content: str | None) -> str:
        text = (content or "").strip()
        if not text:
            return "<p></p>"
        if re.search(r"<\s*[a-zA-Z][^>]*>", text):
            return text
        paragraphs = [segment.strip() for segment in re.split(r"\n\s*\n", text) if segment.strip()]
        if not paragraphs:
            paragraphs = [text]
        return "".join(f"<p>{escape(paragraph).replace(chr(10), '<br/>')}</p>" for paragraph in paragraphs)

    def _sanitize_html(self, html: str) -> str:
        sanitized = re.sub(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", "", html, flags=re.IGNORECASE | re.DOTALL)
        sanitized = re.sub(r'\s+on[a-zA-Z-]+\s*=\s*("[^"]*"|\'[^\']*\')', "", sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'\s+style\s*=\s*("[^"]*"|\'[^\']*\')', "", sanitized, flags=re.IGNORECASE)

        def _replace_tag(match: re.Match[str]) -> str:
            tag_name = (match.group(2) or "").lower()
            if tag_name not in _ALLOWED_TAGS:
                return ""
            closing = "/" if match.group(1) else ""
            if closing:
                return f"</{tag_name}>"
            attrs = ""
            raw_attrs = match.group(3) or ""
            if tag_name == "a":
                href_match = re.search(r'href\s*=\s*("[^"]*"|\'[^\']*\')', raw_attrs, flags=re.IGNORECASE)
                if href_match:
                    attrs = f" href={href_match.group(1)}"
            elif tag_name == "img":
                attr_parts: list[str] = []
                for attr_name in ("src", "alt", "title"):
                    attr_match = re.search(rf'{attr_name}\s*=\s*("[^"]*"|\'[^\']*\')', raw_attrs, flags=re.IGNORECASE)
                    if attr_match:
                        attr_parts.append(f" {attr_name}={attr_match.group(1)}")
                attrs = "".join(attr_parts)
            if tag_name in _VOID_TAGS:
                return f"<{tag_name}{attrs}/>"
            return f"<{tag_name}{attrs}>"

        return re.sub(r"<\s*(/)?\s*([a-zA-Z0-9]+)([^>]*)>", _replace_tag, sanitized)

    def _extract_plain_text(self, html: str) -> str:
        parser = _HtmlTextExtractor()
        parser.feed(html)
        parser.close()
        return parser.get_text()

    def _estimate_block_units(self, block_html: str) -> int:
        text = self._extract_plain_text(block_html)
        if not text:
            return 80
        heading_bonus = 140 if re.match(r"^<h[1-6][^>]*>", block_html, flags=re.IGNORECASE) else 0
        image_bonus = 320 if re.match(r"^<img[^>]*?/?>$", block_html.strip(), flags=re.IGNORECASE) else 0
        line_bonus = 50 * max(text.count("\n"), 0)
        return max(100, len(text) + heading_bonus + image_bonus + line_bonus)

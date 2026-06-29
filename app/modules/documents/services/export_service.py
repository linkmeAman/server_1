from __future__ import annotations

import base64
import io
import re
import textwrap
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

from app.modules.documents.schemas import DocumentExportJob, DocumentSnapshot


@dataclass
class TextRun:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False


@dataclass
class RichBlock:
    kind: str
    runs: list[TextRun]
    level: int = 0
    ordered: bool = False
    list_level: int = 0
    index: int = 0
    src: str | None = None
    alt: str | None = None
    title: str | None = None


@dataclass
class DocxImageAsset:
    rel_id: str
    target: str
    content_type: str
    payload: bytes
    width_emu: int
    height_emu: int


class _InlineParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.runs: list[TextRun] = []
        self._stack: list[dict[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.runs.append(TextRun("\n", **self._style()))
            return
        if tag in {"strong", "b", "em", "i", "u", "s", "code"}:
            style = self._style()
            if tag in {"strong", "b"}:
                style["bold"] = True
            elif tag in {"em", "i"}:
                style["italic"] = True
            elif tag == "u":
                style["underline"] = True
            elif tag == "s":
                style["strike"] = True
            elif tag == "code":
                style["code"] = True
            self._stack.append(style)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"strong", "b", "em", "i", "u", "s", "code"} and self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if data:
            self.runs.append(TextRun(data, **self._style()))

    def _style(self) -> dict[str, bool]:
        if not self._stack:
            return {
                "bold": False,
                "italic": False,
                "underline": False,
                "strike": False,
                "code": False,
            }
        return dict(self._stack[-1])


class _ListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[tuple[int, bool, int, str]] = []
        self._stack: list[dict[str, int | bool]] = []
        self._li_depth = 0
        self._parts: list[str] = []
        self._level = 0
        self._ordered = False
        self._index = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"ul", "ol"}:
            self._stack.append({"ordered": tag == "ol", "index": 0})
            if self._li_depth > 0:
                self._parts.append(self.get_starttag_text())
            return
        if tag == "li":
            if self._li_depth == 0 and self._stack:
                self._level = max(len(self._stack) - 1, 0)
                self._ordered = bool(self._stack[-1]["ordered"])
                self._stack[-1]["index"] = int(self._stack[-1]["index"]) + 1
                self._index = int(self._stack[-1]["index"])
                self._parts = []
            else:
                self._parts.append(self.get_starttag_text())
            self._li_depth += 1
            return
        if self._li_depth > 0:
            self._parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if tag == "li":
            if self._li_depth > 1:
                self._parts.append(f"</{tag}>")
            self._li_depth -= 1
            if self._li_depth == 0:
                self.items.append((self._level, self._ordered, self._index, "".join(self._parts)))
                self._parts = []
            return
        if tag in {"ul", "ol"}:
            if self._li_depth > 0:
                self._parts.append(f"</{tag}>")
            if self._stack:
                self._stack.pop()
            return
        if self._li_depth > 0:
            self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._li_depth > 0:
            self._parts.append(data)


class ExportService:
    def __init__(self, preview_service) -> None:
        self._preview_service = preview_service

    def export(self, export_format: str, snapshot: DocumentSnapshot, file_stem: str) -> DocumentExportJob:
        fmt = export_format.lower()
        now = datetime.now(timezone.utc)

        if fmt == "html":
            payload = self._wrap_html(snapshot.title, self._build_document_html(snapshot)).encode("utf-8")
            mime_type = "text/html; charset=utf-8"
            file_name = f"{file_stem}.html"
        elif fmt == "markdown":
            payload = self._build_markdown(snapshot).encode("utf-8")
            mime_type = "text/markdown; charset=utf-8"
            file_name = f"{file_stem}.md"
        elif fmt == "text":
            payload = self._build_plain_text(snapshot).encode("utf-8")
            mime_type = "text/plain; charset=utf-8"
            file_name = f"{file_stem}.txt"
        elif fmt == "pdf":
            payload = self._build_pdf_bytes(snapshot)
            mime_type = "application/pdf"
            file_name = f"{file_stem}.pdf"
        elif fmt == "docx":
            payload = self._build_docx_bytes(snapshot)
            mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            file_name = f"{file_stem}.docx"
        else:
            payload = self._wrap_html(snapshot.title, self._build_document_html(snapshot)).encode("utf-8")
            mime_type = "text/html; charset=utf-8"
            file_name = f"{file_stem}.html"
            fmt = "html"

        return DocumentExportJob(
            id=str(uuid4()),
            document_id=None,
            document_version_id=None,
            format=fmt,
            status="completed",
            mime_type=mime_type,
            file_name=file_name,
            artifact_base64=base64.b64encode(payload).decode("ascii"),
            created_at=now,
            completed_at=now,
        )

    def _build_plain_text(self, snapshot: DocumentSnapshot) -> str:
        lines = [snapshot.title.strip()]
        for block in self._blocks(snapshot):
            rendered = self._plain_block(block)
            if rendered:
                lines.append("")
                lines.extend(rendered.splitlines())
        return "\n".join(lines).strip() + "\n"

    def _build_markdown(self, snapshot: DocumentSnapshot) -> str:
        parts = [f"# {snapshot.title.strip()}"]
        for block in self._blocks(snapshot):
            rendered = self._markdown_block(block)
            if rendered:
                parts.append(rendered)
        return "\n\n".join(parts).strip() + "\n"

    def _wrap_html(self, title: str, body_html: str) -> str:
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"/>"
            f"<title>{escape(title)}</title>"
            "<style>"
            "body{margin:0;background:#f3f4f6;color:#0f172a;font-family:Georgia,'Times New Roman',serif;}"
            ".shell{max-width:860px;margin:0 auto;padding:48px 24px 80px;}"
            ".doc{background:white;border-radius:24px;padding:48px;box-shadow:0 24px 80px rgba(15,23,42,.12);}"
            "h1{font-size:2.4rem;margin:0 0 24px;}h2{font-size:1.35rem;margin:32px 0 16px;}h3{font-size:1.1rem;margin:24px 0 12px;}"
            "p,li,blockquote,pre{line-height:1.7;font-size:1rem;}blockquote{margin:16px 0;padding-left:16px;border-left:4px solid #cbd5e1;color:#334155;}"
            "pre{padding:16px;background:#e2e8f0;border-radius:16px;overflow:auto;white-space:pre-wrap;}"
            "img{display:block;max-width:100%;margin:18px 0;border-radius:18px;box-shadow:0 18px 50px rgba(15,23,42,.14);}"
            "</style></head><body><main class=\"shell\"><article class=\"doc\">"
            f"<h1>{escape(title)}</h1>{body_html}</article></main></body></html>"
        )

    def _build_document_html(self, snapshot: DocumentSnapshot) -> str:
        sections: list[str] = []
        for section in snapshot.sections:
            html = self._preview_service._normalize_content_html(section.content)  # noqa: SLF001
            html = self._preview_service._sanitize_html(html)  # noqa: SLF001
            sections.append(f"<section><h2>{escape(section.title)}</h2>{html}</section>")
        return "".join(sections)

    def _blocks(self, snapshot: DocumentSnapshot) -> list[RichBlock]:
        blocks: list[RichBlock] = []
        for section in snapshot.sections:
            blocks.append(RichBlock(kind="heading", runs=[TextRun(section.title)], level=2))
            for fragment in self._preview_service._collect_block_html(section.content):  # noqa: SLF001
                blocks.extend(self._fragment_to_blocks(fragment))
        return blocks

    def _fragment_to_blocks(self, html: str) -> list[RichBlock]:
        stripped = html.strip()
        tag_match = re.match(r"<([a-zA-Z0-9]+)", stripped)
        if not tag_match:
            text = self._preview_service._extract_plain_text(stripped)  # noqa: SLF001
            return [RichBlock(kind="paragraph", runs=[TextRun(text)])] if text else []

        tag = tag_match.group(1).lower()
        if tag == "img":
            attrs = self._attrs(stripped)
            return [RichBlock(kind="image", runs=[], src=attrs.get("src"), alt=attrs.get("alt"), title=attrs.get("title"))]
        if tag in {"ul", "ol"}:
            return self._list_blocks(stripped)

        inner = re.sub(r"^<[^>]+>|</[^>]+>$", "", stripped, flags=re.IGNORECASE | re.DOTALL)
        if tag == "pre":
            text = self._preview_service._extract_plain_text(stripped)  # noqa: SLF001
            return [RichBlock(kind="pre", runs=[TextRun(text, code=True)])]

        runs = self._html_to_runs(inner)
        if not runs:
            return []
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            return [RichBlock(kind="heading", runs=runs, level=int(tag[1]))]
        if tag == "blockquote":
            return [RichBlock(kind="quote", runs=runs)]
        return [RichBlock(kind="paragraph", runs=runs)]

    def _html_to_runs(self, html: str) -> list[TextRun]:
        parser = _InlineParser()
        parser.feed(html)
        parser.close()
        return self._coalesce(parser.runs)

    def _coalesce(self, runs: list[TextRun]) -> list[TextRun]:
        collapsed: list[TextRun] = []
        for run in runs:
            if not run.text:
                continue
            if (
                collapsed
                and collapsed[-1].bold == run.bold
                and collapsed[-1].italic == run.italic
                and collapsed[-1].underline == run.underline
                and collapsed[-1].strike == run.strike
                and collapsed[-1].code == run.code
            ):
                collapsed[-1].text += run.text
            else:
                collapsed.append(run)
        return collapsed

    def _list_blocks(self, html: str) -> list[RichBlock]:
        parser = _ListParser()
        parser.feed(html)
        parser.close()
        return [
            RichBlock(
                kind="list_item",
                runs=self._html_to_runs(item_html),
                ordered=ordered,
                list_level=level,
                index=index,
            )
            for level, ordered, index, item_html in parser.items
        ]

    def _plain_block(self, block: RichBlock) -> str:
        if block.kind == "list_item":
            prefix = f"{'  ' * block.list_level}{block.index}. " if block.ordered else f"{'  ' * block.list_level}- "
            return prefix + self._runs_text(block.runs).strip()
        if block.kind == "image":
            return f"[Image] {block.alt or block.title or block.src or 'Embedded image'}"
        return self._runs_text(block.runs)

    def _markdown_block(self, block: RichBlock) -> str:
        if block.kind == "heading":
            return f"{'#' * min(max(block.level, 1), 6)} {self._runs_markdown(block.runs).strip()}"
        if block.kind == "quote":
            lines = self._runs_markdown(block.runs).splitlines() or [""]
            return "\n".join(f"> {line}" for line in lines)
        if block.kind == "pre":
            return f"```\n{self._runs_text(block.runs)}\n```"
        if block.kind == "list_item":
            prefix = f"{'  ' * block.list_level}{block.index}. " if block.ordered else f"{'  ' * block.list_level}- "
            return prefix + self._runs_markdown(block.runs).strip()
        if block.kind == "image":
            return f"![{escape(block.alt or block.title or 'image')}]({block.src or ''})"
        return self._runs_markdown(block.runs).strip()

    def _runs_text(self, runs: list[TextRun]) -> str:
        text = "".join(run.text for run in runs).replace("\xa0", " ")
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _runs_markdown(self, runs: list[TextRun]) -> str:
        parts: list[str] = []
        for run in runs:
            text = run.text.replace("\xa0", " ")
            if not text:
                continue
            value = text
            if run.code:
                value = f"`{value}`"
            else:
                if run.bold:
                    value = f"**{value}**"
                if run.italic:
                    value = f"*{value}*"
                if run.underline:
                    value = f"<u>{value}</u>"
                if run.strike:
                    value = f"~~{value}~~"
            parts.append(value)
        return "".join(parts).strip()

    def _pdf_lines(self, snapshot: DocumentSnapshot) -> list[tuple[str, str, int, int, int]]:
        lines: list[tuple[str, str, int, int, int]] = []
        lines.extend(self._wrap_pdf(snapshot.title.strip(), "F2", 24, 72, 56, 8))
        for block in self._blocks(snapshot):
            if block.kind == "heading":
                lines.extend(self._wrap_pdf(self._runs_text(block.runs), "F2", 18 if block.level <= 2 else 14, 72, 64, 4))
            elif block.kind == "quote":
                lines.extend(self._wrap_pdf(self._runs_text(block.runs), "F3", 11, 96, 58, 2))
            elif block.kind == "pre":
                for pre_line in self._runs_text(block.runs).splitlines() or [""]:
                    lines.extend(self._wrap_pdf(pre_line, "F4", 9, 96, 72, 0))
                lines.append(("", "F1", 10, 72, 4))
            elif block.kind == "list_item":
                prefix = f"{block.index}. " if block.ordered else "- "
                lines.extend(self._wrap_pdf(prefix + self._runs_text(block.runs), "F1", 11, 96 + (block.list_level * 18), 64 - (block.list_level * 2), 1))
            elif block.kind == "image":
                lines.extend(self._wrap_pdf(f"[Image] {block.alt or block.title or block.src or 'Embedded image'}", "F3", 10, 96, 60, 2))
            else:
                lines.extend(self._wrap_pdf(self._runs_text(block.runs), "F1", 11, 72, 68, 2))
        return lines

    def _wrap_pdf(self, text: str, font: str, size: int, indent: int, width: int, after: int) -> list[tuple[str, str, int, int, int]]:
        wrapped = textwrap.wrap(text or "", width=max(width, 16), break_long_words=False, replace_whitespace=False) or [""]
        return [(line, font, size, indent, after if index == len(wrapped) - 1 else 0) for index, line in enumerate(wrapped)]


    def _build_pdf_bytes(self, snapshot: DocumentSnapshot) -> bytes:
        lines = self._pdf_lines(snapshot) or [(snapshot.title.strip(), "F2", 24, 72, 8)]
        pages: list[list[tuple[str, str, int, int, int]]] = []
        page: list[tuple[str, str, int, int, int]] = []
        y = 760
        for text, font, size, x, after in lines:
            line_h = max(size + 4, 14)
            if y - line_h < 54:
                pages.append(page)
                page = []
                y = 760
            page.append((text, font, size, x, y))
            y -= line_h + after
        if page:
            pages.append(page)

        buffer = io.BytesIO()
        offsets: list[int] = []

        def write_obj(number: int, payload: bytes) -> None:
            offsets.append(buffer.tell())
            buffer.write(f"{number} 0 obj\n".encode("latin-1"))
            buffer.write(payload)
            buffer.write(b"\nendobj\n")

        buffer.write(b"%PDF-1.4\n")
        fonts = {
            "F1": b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            "F2": b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
            "F3": b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique >>",
            "F4": b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        }
        font_ids = {key: index + 1 for index, key in enumerate(fonts)}
        next_id = len(font_ids) + 1
        page_ids = list(range(next_id, next_id + len(pages)))
        content_ids = list(range(next_id + len(pages), next_id + (2 * len(pages))))
        pages_id = content_ids[-1] + 1
        catalog_id = pages_id + 1

        for key, payload in fonts.items():
            write_obj(font_ids[key], payload)

        for index, page_lines in enumerate(pages):
            ops: list[str] = []
            for text, font, size, x, y_pos in page_lines:
                safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                ops.extend(["BT", f"/{font} {size} Tf", f"1 0 0 1 {x} {y_pos} Tm", f"({safe}) Tj", "ET"])
            stream = "\n".join(ops).encode("latin-1", errors="replace")
            write_obj(content_ids[index], f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")
            write_obj(
                page_ids[index],
                (
                    f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {font_ids['F1']} 0 R /F2 {font_ids['F2']} 0 R /F3 {font_ids['F3']} 0 R /F4 {font_ids['F4']} 0 R >> >> "
                    f"/Contents {content_ids[index]} 0 R >>"
                ).encode("latin-1"),
            )

        kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
        write_obj(pages_id, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1"))
        write_obj(catalog_id, f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1"))
        xref = buffer.tell()
        buffer.write(f"xref\n0 {catalog_id + 1}\n".encode("latin-1"))
        buffer.write(b"0000000000 65535 f \n")
        for offset in offsets:
            buffer.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
        buffer.write((f"trailer\n<< /Size {catalog_id + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF").encode("latin-1"))
        return buffer.getvalue()

    def _build_docx_bytes(self, snapshot: DocumentSnapshot) -> bytes:
        images: list[DocxImageAsset] = []
        document_xml = self._build_document_xml(snapshot, images)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", self._content_types(images))
            archive.writestr(
                "_rels/.rels",
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
                "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>"
                "</Relationships>",
            )
            archive.writestr("word/document.xml", document_xml)
            archive.writestr("word/styles.xml", self._styles_xml())
            archive.writestr("word/_rels/document.xml.rels", self._document_rels(images))
            for index, image in enumerate(images, start=1):
                archive.writestr(f"word/media/image{index}{self._ext_for_mime(image.content_type)}", image.payload)
        return output.getvalue()

    def _build_document_xml(self, snapshot: DocumentSnapshot, images: list[DocxImageAsset]) -> str:
        paragraphs = [self._xml_paragraph([TextRun(snapshot.title)], style="Title")]
        for block in self._blocks(snapshot):
            if block.kind == "image":
                image = self._docx_image(block, len(images) + 1)
                if image is None:
                    paragraphs.append(self._xml_paragraph([TextRun(f"[Image] {block.alt or block.title or block.src or 'Embedded image'}")]))
                else:
                    images.append(image)
                    paragraphs.append(self._xml_image(image))
                continue
            paragraphs.append(self._xml_block(block))
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" "
            "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" "
            "xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\" "
            "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" "
            "xmlns:pic=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
            f"<w:body>{''.join(paragraphs)}{self._section_xml(snapshot)}</w:body></w:document>"
        )

    def _xml_block(self, block: RichBlock) -> str:
        if block.kind == "heading":
            return self._xml_paragraph(block.runs, style=f"Heading{min(max(block.level, 1), 3)}")
        if block.kind == "quote":
            return self._xml_paragraph(block.runs, indent_left=720, italic_default=True, spacing_before=120, spacing_after=120)
        if block.kind == "pre":
            return self._xml_paragraph(block.runs, font_name="Courier New", indent_left=360, spacing_before=120, spacing_after=120)
        if block.kind == "list_item":
            prefix = f"{block.index}. " if block.ordered else "• "
            return self._xml_paragraph([TextRun(prefix)] + block.runs, indent_left=360 + (block.list_level * 360), hanging=240)
        return self._xml_paragraph(block.runs)

    def _xml_paragraph(
        self,
        runs: list[TextRun],
        style: str | None = None,
        font_name: str | None = None,
        indent_left: int | None = None,
        hanging: int | None = None,
        spacing_before: int | None = None,
        spacing_after: int | None = None,
        italic_default: bool = False,
    ) -> str:
        ppr: list[str] = []
        if style:
            ppr.append(f'<w:pStyle w:val="{escape(style)}"/>')
        if indent_left is not None or hanging is not None:
            attrs: list[str] = []
            if indent_left is not None:
                attrs.append(f'w:left="{indent_left}"')
            if hanging is not None:
                attrs.append(f'w:hanging="{hanging}"')
            ppr.append(f'<w:ind {" ".join(attrs)}/>')
        if spacing_before is not None or spacing_after is not None:
            attrs = []
            if spacing_before is not None:
                attrs.append(f'w:before="{spacing_before}"')
            if spacing_after is not None:
                attrs.append(f'w:after="{spacing_after}"')
            ppr.append(f'<w:spacing {" ".join(attrs)}/>')
        ppr_xml = f"<w:pPr>{''.join(ppr)}</w:pPr>" if ppr else ""
        runs_xml = "".join(self._xml_run(run, font_name, italic_default) for run in runs)
        return f"<w:p>{ppr_xml}{runs_xml}</w:p>"

    def _xml_run(self, run: TextRun, font_name: str | None, italic_default: bool) -> str:
        pieces = run.text.split("\n")
        xml_parts: list[str] = []
        for index, piece in enumerate(pieces):
            rpr: list[str] = []
            if run.bold:
                rpr.append("<w:b/>")
            if run.italic or italic_default:
                rpr.append("<w:i/>")
            if run.underline:
                rpr.append('<w:u w:val="single"/>')
            if run.strike:
                rpr.append("<w:strike/>")
            if run.code or font_name:
                chosen_font = font_name or "Courier New"
                rpr.append(f'<w:rFonts w:ascii="{escape(chosen_font)}" w:hAnsi="{escape(chosen_font)}"/>')
            rpr_xml = f"<w:rPr>{''.join(rpr)}</w:rPr>" if rpr else ""
            xml_parts.append(f'<w:r>{rpr_xml}<w:t xml:space="preserve">{escape(piece)}</w:t></w:r>')
            if index < len(pieces) - 1:
                xml_parts.append(f"<w:r>{rpr_xml}<w:br/></w:r>")
        return "".join(xml_parts) or '<w:r><w:t xml:space="preserve"></w:t></w:r>'

    def _xml_image(self, image: DocxImageAsset) -> str:
        return (
            "<w:p><w:r><w:drawing><wp:inline distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">"
            f"<wp:extent cx=\"{image.width_emu}\" cy=\"{image.height_emu}\"/>"
            "<wp:docPr id=\"1\" name=\"Document Image\"/>"
            "<a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
            "<pic:pic><pic:nvPicPr><pic:cNvPr id=\"0\" name=\"Image\"/><pic:cNvPicPr/></pic:nvPicPr>"
            f"<pic:blipFill><a:blip r:embed=\"{image.rel_id}\"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
            "<pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/>"
            f"<a:ext cx=\"{image.width_emu}\" cy=\"{image.height_emu}\"/></a:xfrm>"
            "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr>"
            "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        )

    def _section_xml(self, snapshot: DocumentSnapshot) -> str:
        layout = snapshot.sections[0].layout if snapshot.sections else None
        page_size = (layout.page_size if layout else "A4").upper()
        orientation = (layout.orientation if layout else "portrait").lower()
        margins = layout.margins if layout else None
        page_map = {"A4": (11906, 16838), "A3": (16838, 23811), "LETTER": (12240, 15840), "LEGAL": (12240, 20160)}
        width, height = page_map.get(page_size, page_map["A4"])
        if orientation == "landscape":
            width, height = height, width
        top = int(round((margins.top if margins else 72) * 20))
        right = int(round((margins.right if margins else 72) * 20))
        bottom = int(round((margins.bottom if margins else 72) * 20))
        left = int(round((margins.left if margins else 72) * 20))
        orient_attr = ' w:orient="landscape"' if orientation == "landscape" else ""
        return f'<w:sectPr><w:pgSz w:w="{width}" w:h="{height}"{orient_attr}/><w:pgMar w:top="{top}" w:right="{right}" w:bottom="{bottom}" w:left="{left}"/></w:sectPr>'

    def _content_types(self, images: list[DocxImageAsset]) -> str:
        defaults = {"rels": "application/vnd.openxmlformats-package.relationships+xml", "xml": "application/xml"}
        for image in images:
            defaults[self._ext_for_mime(image.content_type).lstrip('.')] = image.content_type
        defaults_xml = "".join(f'<Default Extension="{ext}" ContentType="{content_type}"/>' for ext, content_type in defaults.items())
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
            f"{defaults_xml}"
            "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
            "<Override PartName=\"/word/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml\"/>"
            "</Types>"
        )

    def _document_rels(self, images: list[DocxImageAsset]) -> str:
        rels = ['<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>']
        for index, image in enumerate(images, start=1):
            rels.append(f'<Relationship Id="{image.rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image{index}{self._ext_for_mime(image.content_type)}"/>')
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            f"{''.join(rels)}"
            "</Relationships>"
        )

    def _styles_xml(self) -> str:
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<w:styles xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
            '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:sz w:val="30"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>'
            "</w:styles>"
        )

    def _docx_image(self, block: RichBlock, index: int) -> DocxImageAsset | None:
        if not block.src:
            return None
        parsed = re.match(r"data:([\w/+.-]+);base64,(.+)", block.src, flags=re.DOTALL)
        if not parsed:
            return None
        try:
            payload = base64.b64decode(parsed.group(2))
        except Exception:
            return None
        mime = parsed.group(1)
        if mime not in {"image/png", "image/jpeg", "image/gif"}:
            return None
        width, height = self._image_size(payload, mime)
        width_emu = max(int(width * 9525), 1)
        height_emu = max(int(height * 9525), 1)
        if width_emu > 5486400:
            ratio = 5486400 / width_emu
            width_emu = 5486400
            height_emu = max(int(height_emu * ratio), 1)
        return DocxImageAsset(rel_id=f"rIdImage{index}", target=f"media/image{index}{self._ext_for_mime(mime)}", content_type=mime, payload=payload, width_emu=width_emu, height_emu=height_emu)

    def _ext_for_mime(self, mime: str) -> str:
        return {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}.get(mime, ".bin")

    def _image_size(self, payload: bytes, mime: str) -> tuple[int, int]:
        try:
            if mime == "image/png" and payload[:8] == b"\x89PNG\r\n\x1a\n":
                return int.from_bytes(payload[16:20], "big"), int.from_bytes(payload[20:24], "big")
            if mime == "image/gif" and payload[:6] in {b"GIF87a", b"GIF89a"}:
                return int.from_bytes(payload[6:8], "little"), int.from_bytes(payload[8:10], "little")
            if mime == "image/jpeg":
                index = 2
                while index < len(payload) - 9:
                    if payload[index] != 0xFF:
                        index += 1
                        continue
                    marker = payload[index + 1]
                    index += 2
                    if marker in {0xD8, 0xD9}:
                        continue
                    length = int.from_bytes(payload[index:index + 2], "big")
                    if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                        return int.from_bytes(payload[index + 5:index + 7], "big"), int.from_bytes(payload[index + 3:index + 5], "big")
                    index += length
        except Exception:
            pass
        return 640, 480

    def _attrs(self, html: str) -> dict[str, str]:
        attrs: dict[str, str] = {}
        for name, value1, value2 in re.findall(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', html):
            attrs[name.lower()] = value1 or value2 or ""
        return attrs

"""
distill.parsers.html
~~~~~~~~~~~~~~~~~~~~
Parser for HTML documents (.html, .htm).

Primary path:   stdlib html.parser (always available)
Content extraction: trafilatura (boilerplate removal), readability-lxml (fallback)

Install:
    pip install "distill-core[html]"   # adds trafilatura + readability-lxml
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from html.parser import HTMLParser as StdlibHTMLParser
from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    BlockQuote, CodeBlock, Document, DocumentMetadata,
    Image, ImageType, List, ListItem, Paragraph,
    Section, Table, TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseOptions, Parser, UnsupportedFormatError
from distill.registry import registry

log = logging.getLogger(__name__)


# ── Content extraction layer ─────────────────────────────────────────────────

class HTMLContentExtractor:
    """
    Strips boilerplate (nav, footer, ads) from raw HTML and returns the
    main content as an HTML string.

    When extract_content=False, returns raw_html unchanged.
    When extract_content=True, tries trafilatura first, falls back to
    readability-lxml, and falls back to raw_html if both fail.
    The method never raises.
    """

    def __init__(self, collector=None) -> None:
        # collector is Optional[WarningCollector]; guarded on every call
        self._collector = collector

    def extract(self, raw_html: str, extract_content: bool = False) -> str:
        if not extract_content:
            return raw_html

        # Try trafilatura
        try:
            import trafilatura
            result = trafilatura.extract(
                raw_html,
                include_tables=True,
                include_links=True,
                output_format="html",
            )
            if result:
                return result
        except Exception as exc:
            log.debug("trafilatura failed: %s", exc)

        # Try readability-lxml
        try:
            from readability import Document as ReadabilityDoc
            doc = ReadabilityDoc(raw_html)
            result = doc.summary()
            if result:
                return result
        except Exception as exc:
            log.debug("readability-lxml failed: %s", exc)

        # Both failed — emit warning and return raw HTML
        try:
            if self._collector is not None:
                from distill.warnings import ConversionWarning, WarningType
                self._collector.add(ConversionWarning(
                    type=WarningType.CONTENT_EXTRACTED,
                    message="Content extraction failed; raw HTML used as-is.",
                ))
        except Exception:
            pass

        return raw_html


# ── DOM walker helpers ────────────────────────────────────────────────────────

def _text(elem) -> str:
    """Return all text content under elem, stripped."""
    return "".join(elem.itertext()).strip()


def _parse_runs(elem) -> list[TextRun]:
    """Extract inline TextRun objects from an element, propagating formatting."""
    runs: list[TextRun] = []

    def walk(node, bold=False, italic=False, code=False, strike=False, href=None):
        tag = (node.tag or "").lower().split("}")[-1]  # strip namespace

        c_bold   = bold   or tag in ("strong", "b")
        c_italic = italic or tag in ("em", "i")
        c_code   = code   or tag in ("code",)
        c_strike = strike or tag in ("del", "s", "strike")
        c_href   = node.get("href") if tag == "a" else href

        if node.text:
            runs.append(TextRun(
                text=node.text,
                bold=c_bold, italic=c_italic, code=c_code,
                strikethrough=c_strike, href=c_href,
            ))
        for child in node:
            walk(child, c_bold, c_italic, c_code, c_strike, c_href)
            if child.tail:
                runs.append(TextRun(
                    text=child.tail,
                    bold=bold, italic=italic, code=code,
                    strikethrough=strike, href=href,
                ))

    walk(elem)
    return [r for r in runs if r.text]


def _parse_list(elem, depth: int = 0) -> List:
    """Parse <ul>/<ol> into an IR List, nesting up to 3 levels."""
    ordered = (elem.tag or "").lower().split("}")[-1] == "ol"
    items: list[ListItem] = []
    for child in elem:
        tag = (child.tag or "").lower().split("}")[-1]
        if tag != "li":
            continue
        nested: list[List] = []
        if depth < 2:
            for sub in child:
                sub_tag = (sub.tag or "").lower().split("}")[-1]
                if sub_tag in ("ul", "ol"):
                    nested.append(_parse_list(sub, depth + 1))
        content = _parse_runs(child)
        if not content:
            t = _text(child)
            if t:
                content = [TextRun(t)]
        items.append(ListItem(content=content, children=nested))
    return List(items=items, ordered=ordered)


def _parse_table(elem) -> Table:
    """Parse a <table> element into an IR Table."""
    rows: list[TableRow] = []

    def parse_row(tr, is_header: bool) -> TableRow:
        cells = []
        for td in tr:
            td_tag = (td.tag or "").lower().split("}")[-1]
            if td_tag not in ("td", "th"):
                continue
            cell_is_header = is_header or td_tag == "th"
            content_runs = _parse_runs(td)
            if not content_runs:
                t = _text(td)
                if t:
                    content_runs = [TextRun(t)]
            cells.append(TableCell(content=content_runs, is_header=cell_is_header))
        return TableRow(cells=cells)

    for child in elem:
        child_tag = (child.tag or "").lower().split("}")[-1]
        if child_tag in ("thead", "tbody", "tfoot"):
            is_header_section = child_tag == "thead"
            for tr in child:
                if (tr.tag or "").lower().split("}")[-1] == "tr":
                    rows.append(parse_row(tr, is_header_section))
        elif child_tag == "tr":
            rows.append(parse_row(child, False))

    # If no explicit thead, treat first row as header
    if rows and not any(c.is_header for c in (rows[0].cells or [])):
        for cell in rows[0].cells:
            cell.is_header = True

    return Table(rows=rows)


def _html_to_etree(raw: str):
    """
    Parse raw HTML to an element tree using lxml if available,
    falling back to stdlib ElementTree (lenient).
    """
    try:
        import lxml.html as lxml_html
        root = lxml_html.fromstring(raw)
        return root
    except Exception:
        pass

    # Fallback: strip doctype and troublesome entities, wrap in root element
    try:
        import re
        cleaned = re.sub(r'<!DOCTYPE[^>]*>', '', raw, flags=re.IGNORECASE)
        cleaned = re.sub(r'&(?!(amp|lt|gt|quot|apos);)(\w+);', '', cleaned)
        wrapped = f"<root>{cleaned}</root>"
        return ET.fromstring(wrapped)
    except Exception:
        return ET.fromstring("<root></root>")


# ── Parser ────────────────────────────────────────────────────────────────────

@registry.register
class HTMLParser(Parser):
    """
    Parses .html / .htm files into an IR Document.

    Content extraction (boilerplate removal) is opt-in via
    options.extra.get("extract_content", False).
    """

    extensions = [".html", ".htm"]
    mime_types = ["text/html"]
    requires   = []   # stdlib html.parser is sufficient

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()

        # Read source
        if isinstance(source, bytes):
            raw = source.decode("utf-8", errors="replace")
        elif isinstance(source, (str, Path)):
            path = Path(source)
            if path.exists():
                raw = path.read_text(encoding="utf-8", errors="replace")
            else:
                raw = str(source)
        else:
            raw = str(source)

        # Content extraction (boilerplate removal)
        collector     = getattr(options, "collector", None)
        extract_flag  = (options.extra or {}).get("extract_content", False)
        extractor     = HTMLContentExtractor(collector=collector)
        html_content  = extractor.extract(raw, extract_content=bool(extract_flag))

        # Parse DOM
        root = _html_to_etree(html_content)

        # Walk DOM and build IR
        sections: list[Section] = []
        current_section: Optional[Section] = None

        HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
        SKIP_TAGS    = {"script", "style", "meta", "link", "head"}

        def get_tag(elem) -> str:
            return (elem.tag or "").lower().split("}")[-1]

        def flush_or_create_section(level: int, heading_runs: list[TextRun]) -> Section:
            sec = Section(level=level, heading=heading_runs, blocks=[])
            sections.append(sec)
            return sec

        def process_children(parent_elem):
            nonlocal current_section
            for elem in parent_elem:
                tag = get_tag(elem)

                if tag in SKIP_TAGS:
                    continue

                if tag in HEADING_TAGS:
                    level = HEADING_TAGS[tag]
                    runs  = _parse_runs(elem)
                    if not runs:
                        t = _text(elem)
                        if t:
                            runs = [TextRun(t)]
                    current_section = flush_or_create_section(level, runs)

                elif tag == "p":
                    runs = _parse_runs(elem)
                    if not runs:
                        t = _text(elem)
                        if t:
                            runs = [TextRun(t)]
                    if runs:
                        if current_section is None:
                            current_section = Section(level=1, heading=None, blocks=[])
                            sections.append(current_section)
                        current_section.blocks.append(Paragraph(runs=runs))

                elif tag in ("ul", "ol"):
                    lst = _parse_list(elem)
                    if lst.items:
                        if current_section is None:
                            current_section = Section(level=1, heading=None, blocks=[])
                            sections.append(current_section)
                        current_section.blocks.append(lst)

                elif tag == "table":
                    tbl = _parse_table(elem)
                    if tbl.rows:
                        if current_section is None:
                            current_section = Section(level=1, heading=None, blocks=[])
                            sections.append(current_section)
                        current_section.blocks.append(tbl)

                elif tag in ("pre", "code"):
                    code_text = _text(elem)
                    if code_text:
                        if current_section is None:
                            current_section = Section(level=1, heading=None, blocks=[])
                            sections.append(current_section)
                        current_section.blocks.append(CodeBlock(code=code_text))

                elif tag == "img":
                    alt = elem.get("alt") or ""
                    img = Image(image_type=ImageType.UNKNOWN, alt_text=alt or None)
                    if current_section is None:
                        current_section = Section(level=1, heading=None, blocks=[])
                        sections.append(current_section)
                    current_section.blocks.append(img)

                elif tag in ("div", "article", "main", "section", "body", "html",
                             "root", "header", "footer", "nav", "aside"):
                    process_children(elem)

                else:
                    # Unknown tag — extract text as paragraph, never raise
                    try:
                        t = _text(elem)
                        if t:
                            if current_section is None:
                                current_section = Section(level=1, heading=None, blocks=[])
                                sections.append(current_section)
                            current_section.blocks.append(Paragraph(runs=[TextRun(t)]))
                    except Exception:
                        pass

        process_children(root)

        # Capture source word count from cleaned HTML
        import re as _re
        try:
            stripped = _re.sub(r'<[^>]+>', ' ', html_content)
            wc = len(stripped.split())
            word_count = wc or None
        except Exception:
            word_count = None

        return Document(
            metadata=DocumentMetadata(source_format="html", word_count=word_count),
            sections=sections,
        )

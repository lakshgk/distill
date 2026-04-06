"""
distill.parsers.json_parser
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Parser for JSON documents (.json).

Named json_parser.py (not json.py) to avoid shadowing the stdlib json module.
Detects the structural type of the JSON and renders it appropriately:
  - JSON Schema → tables of properties with nested definitions
  - Array of objects → tabular rows
  - Flat object (scalar values only) → two-column key/value table
  - Everything else → fenced code block
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    CodeBlock, Document, DocumentMetadata, Paragraph,
    Section, Table, TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseOptions, Parser
from distill.registry import registry
from distill.warnings import ConversionWarning, WarningType

log = logging.getLogger(__name__)


# ── Type detection ───────────────────────────────────────────────────────────

def _detect_json_type(data) -> str:
    """
    Classify a parsed JSON value into one of four structural types.

    Returns:
        "schema"      – dict that looks like a JSON Schema
        "array_dump"  – list where every item is a dict
        "flat_object" – dict with only scalar leaf values
        "code"        – everything else
    """
    if isinstance(data, dict):
        # Schema detection: $schema key, or ($defs / definitions), or (properties + type)
        if "$schema" in data:
            return "schema"
        if "$defs" in data or "definitions" in data:
            return "schema"
        if "properties" in data and "type" in data:
            return "schema"

        # Flat object: every value is a scalar
        if data and all(
            isinstance(v, (str, int, float, bool, type(None)))
            for v in data.values()
        ):
            return "flat_object"

    if isinstance(data, list):
        if data and all(isinstance(item, dict) for item in data):
            return "array_dump"

    return "code"


# ── Word count helper ────────────────────────────────────────────────────────

def _count_words(data) -> int:
    """Sum whitespace-delimited words across all string leaf values."""
    count = 0
    if isinstance(data, str):
        count += len(data.split())
    elif isinstance(data, dict):
        for v in data.values():
            count += _count_words(v)
    elif isinstance(data, list):
        for item in data:
            count += _count_words(item)
    return count


# ── Rendering helpers ────────────────────────────────────────────────────────

def _make_text_cell(text: str, is_header: bool = False) -> TableCell:
    """Create a TableCell containing a single TextRun."""
    return TableCell(
        content=[TextRun(text=str(text))],
        is_header=is_header,
    )


def _type_label(schema_value) -> str:
    """Extract a human-readable type string from a JSON Schema property."""
    if not isinstance(schema_value, dict):
        return str(schema_value)
    t = schema_value.get("type", "")
    if isinstance(t, list):
        return " | ".join(str(x) for x in t)
    if t == "array" and isinstance(schema_value.get("items"), dict):
        inner = _type_label(schema_value["items"])
        return f"array<{inner}>"
    return str(t) if t else "object"


def _render_schema(data: dict, collector, max_depth: int = 4) -> list[Section]:
    """
    Render a JSON Schema document into IR sections.

    Top-level title/description become metadata (handled by caller).
    properties → Table with columns: name, type, required, description.
    $defs/definitions → level-2 sub-sections, each with their own property table.
    """
    sections: list[Section] = []
    required_set = set(data.get("required", []))

    # Main properties table
    properties = data.get("properties")
    if isinstance(properties, dict) and properties:
        sections.append(_schema_properties_section(
            heading_text="Properties",
            level=1,
            properties=properties,
            required_set=required_set,
        ))

    # $defs / definitions
    defs = data.get("$defs") or data.get("definitions")
    if isinstance(defs, dict) and defs and max_depth > 0:
        defs_section = Section(
            heading=[TextRun(text="Definitions")],
            level=1,
            blocks=[],
            subsections=[],
        )
        for def_name, def_schema in defs.items():
            if not isinstance(def_schema, dict):
                continue
            sub_required = set(def_schema.get("required", []))
            sub_props = def_schema.get("properties")
            if isinstance(sub_props, dict) and sub_props:
                sub_sec = _schema_properties_section(
                    heading_text=def_name,
                    level=2,
                    properties=sub_props,
                    required_set=sub_required,
                )
                # Add description as a leading paragraph if present
                desc = def_schema.get("description")
                if desc:
                    sub_sec.blocks.insert(0, Paragraph(runs=[TextRun(text=desc)]))
                defs_section.subsections.append(sub_sec)
            else:
                # Non-object definition: render as code block
                sub_sec = Section(
                    heading=[TextRun(text=def_name)],
                    level=2,
                    blocks=[CodeBlock(
                        code=json.dumps(def_schema, indent=2, ensure_ascii=False),
                        language="json",
                    )],
                )
                defs_section.subsections.append(sub_sec)
        if defs_section.subsections:
            sections.append(defs_section)

    # Fallback: if no properties and no defs rendered, show as code
    if not sections:
        sections.append(Section(
            level=1,
            heading=None,
            blocks=[CodeBlock(
                code=json.dumps(data, indent=2, ensure_ascii=False),
                language="json",
            )],
        ))

    return sections


def _schema_properties_section(
    heading_text: str,
    level: int,
    properties: dict,
    required_set: set,
) -> Section:
    """Build a Section containing a table of schema properties."""
    header_row = TableRow(cells=[
        _make_text_cell("Name", is_header=True),
        _make_text_cell("Type", is_header=True),
        _make_text_cell("Required", is_header=True),
        _make_text_cell("Description", is_header=True),
    ])
    rows = [header_row]
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            prop_schema = {}
        rows.append(TableRow(cells=[
            _make_text_cell(prop_name),
            _make_text_cell(_type_label(prop_schema)),
            _make_text_cell("yes" if prop_name in required_set else "no"),
            _make_text_cell(prop_schema.get("description", "")),
        ]))
    table = Table(rows=rows)
    return Section(
        heading=[TextRun(text=heading_text)],
        level=level,
        blocks=[table],
    )


def _render_array(
    data: list[dict],
    collector,
    max_rows: int,
) -> list[Section]:
    """
    Render a list-of-dicts as a single table section.

    Headers are the union of all keys (preserving insertion order).
    Rows beyond max_rows are truncated with a TABLE_TRUNCATED warning.
    """
    # Collect headers from union of keys, preserving order
    seen: dict[str, None] = {}
    for obj in data:
        for key in obj:
            if key not in seen:
                seen[key] = None
    headers = list(seen)

    header_row = TableRow(cells=[
        _make_text_cell(h, is_header=True) for h in headers
    ])

    truncated = False
    total_rows = len(data)
    render_data = data
    if max_rows > 0 and total_rows > max_rows:
        render_data = data[:max_rows]
        truncated = True
        if collector is not None:
            try:
                collector.add(ConversionWarning(
                    type=WarningType.TABLE_TRUNCATED,
                    message=f"Array table truncated from {total_rows} to {max_rows} rows.",
                    count=total_rows,
                ))
            except Exception:
                pass

    rows = [header_row]
    for obj in render_data:
        rows.append(TableRow(cells=[
            _make_text_cell(str(obj.get(h, ""))) for h in headers
        ]))

    table = Table(
        rows=rows,
        truncated=truncated,
        total_rows=total_rows if truncated else None,
    )
    return [Section(level=1, heading=None, blocks=[table])]


def _render_flat(data: dict) -> list[Section]:
    """Render a flat key→scalar dict as a two-column table."""
    header_row = TableRow(cells=[
        _make_text_cell("Key", is_header=True),
        _make_text_cell("Value", is_header=True),
    ])
    rows = [header_row]
    for key, value in data.items():
        rows.append(TableRow(cells=[
            _make_text_cell(str(key)),
            _make_text_cell(str(value) if value is not None else ""),
        ]))
    table = Table(rows=rows)
    return [Section(level=1, heading=None, blocks=[table])]


# ── Parser ────────────────────────────────────────────────────────────────────

@registry.register
class JSONParser(Parser):
    """
    Parses .json files into an IR Document.

    Detects the structural type of the JSON content and renders it as
    tables, schema documentation, or a fenced code block as appropriate.
    """

    extensions  = [".json"]
    mime_types  = ["application/json"]
    requires    = []

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()
        collector = getattr(options, "collector", None)

        # ── Read source bytes ────────────────────────────────────────────
        if isinstance(source, bytes):
            raw_bytes = source
        elif isinstance(source, (str, Path)):
            path = Path(source)
            if path.exists():
                raw_bytes = path.read_bytes()
            else:
                # Treat the string itself as JSON content
                raw_bytes = str(source).encode("utf-8")
        else:
            raw_bytes = str(source).encode("utf-8")

        # ── Decode ───────────────────────────────────────────────────────
        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = raw_bytes.decode("latin-1")

        # ── Parse JSON ───────────────────────────────────────────────────
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            log.debug("JSON decode failed: %s", exc)
            if collector is not None:
                try:
                    collector.add(ConversionWarning(
                        type=WarningType.CONTENT_EXTRACTED,
                        message=f"Invalid JSON: {exc}",
                    ))
                except Exception:
                    pass
            return Document(
                metadata=DocumentMetadata(source_format="json"),
                sections=[],
            )

        # ── Route by detected type ───────────────────────────────────────
        json_type = _detect_json_type(data)
        max_rows = getattr(options, "max_table_rows", 500) or 0

        if json_type == "schema":
            sections = _render_schema(data, collector)
            # Pull title/description into metadata
            title = data.get("title") if isinstance(data, dict) else None
            description = data.get("description") if isinstance(data, dict) else None
        elif json_type == "array_dump":
            sections = _render_array(data, collector, max_rows)
            title = None
            description = None
        elif json_type == "flat_object":
            sections = _render_flat(data)
            title = None
            description = None
        else:
            # "code" fallback — render as fenced JSON
            sections = [Section(
                level=1,
                heading=None,
                blocks=[CodeBlock(code=raw_text, language="json")],
            )]
            title = None
            description = None

        word_count = _count_words(data) or None

        metadata = DocumentMetadata(
            source_format="json",
            word_count=word_count,
            title=title,
            description=description,
        )

        return Document(metadata=metadata, sections=sections)

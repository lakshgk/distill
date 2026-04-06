"""
distill.parsers.sql
~~~~~~~~~~~~~~~~~~~
Parser for SQL files (.sql).

Uses sqlparse to tokenise and split SQL into statements.
CREATE TABLE statements are rendered as structured IR Table nodes;
other statements become CodeBlock nodes with language="sql".

Install:
    pip install sqlparse
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    CodeBlock,
    Document,
    DocumentMetadata,
    Paragraph,
    Section,
    Table,
    TableCell,
    TableRow,
    TextRun,
)
from distill.parsers.base import ParseOptions, Parser
from distill.registry import registry
from distill.warnings import ConversionWarning, WarningType

log = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _strip_sql_comments(text: str) -> str:
    """Remove inline -- comments and /* */ block comments from a string."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    return text.strip()


def _extract_leading_comments(sql: str) -> str:
    """
    Extract leading -- line comments that appear before the first SQL keyword.
    Returns the comment text (without the -- prefix), joined by newlines.
    """
    lines = sql.strip().splitlines()
    comment_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("--"):
            comment_lines.append(stripped.lstrip("-").strip())
        elif stripped == "":
            continue
        else:
            break
    return "\n".join(comment_lines)


def _count_words(*texts: str) -> int:
    """Count whitespace-delimited tokens across all provided strings."""
    total = 0
    for t in texts:
        if t:
            total += len(t.split())
    return total


def _normalise_type(raw: str) -> str:
    """Normalise a raw column type token string."""
    return re.sub(r"\s+", " ", raw).strip().upper()


# ── Column extraction from CREATE TABLE ──────────────────────────────────────


def _parse_create_table(statement) -> Optional[dict]:
    """
    Given a sqlparse Statement that is a CREATE TABLE, extract:
      - table_name: str
      - columns: list of dicts with keys name, type, not_null, default, pk
      - constraints: list of str (PRIMARY KEY(...), FOREIGN KEY(...) lines)

    Returns None if extraction fails.
    """
    import sqlparse
    from sqlparse import tokens as T

    sql_text = str(statement).strip()

    # Extract the table name from "CREATE TABLE [IF NOT EXISTS] <name>"
    match = re.search(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+(?:\.\w+)?)[`\"\]]?",
        sql_text,
        re.IGNORECASE,
    )
    if not match:
        return None
    table_name = match.group(1)

    # Find the parenthesised column/constraint block
    paren_match = re.search(r"\((.+)\)\s*;?\s*$", sql_text, re.DOTALL)
    if not paren_match:
        return None

    body = paren_match.group(1)

    # Split on top-level commas (respect nested parentheses)
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())

    columns: list[dict] = []
    constraints: list[str] = []

    for part in parts:
        cleaned = _strip_sql_comments(part).strip()
        if not cleaned:
            continue

        upper = cleaned.upper().lstrip()

        # Standalone constraints
        if upper.startswith("PRIMARY KEY") or upper.startswith("FOREIGN KEY"):
            constraints.append(cleaned)
            continue
        if upper.startswith("UNIQUE") or upper.startswith("CHECK") or upper.startswith("CONSTRAINT"):
            constraints.append(cleaned)
            continue
        if upper.startswith("INDEX") or upper.startswith("KEY"):
            constraints.append(cleaned)
            continue

        # Column definition: name type [constraints...]
        # Strip leading/trailing quotes from the column name
        col_match = re.match(
            r"[`\"\[]?(\w+)[`\"\]]?\s+(.+)", cleaned, re.IGNORECASE | re.DOTALL
        )
        if not col_match:
            continue

        col_name = col_match.group(1)
        remainder = col_match.group(2).strip()

        # Parse out the type (everything before the first constraint keyword)
        # Constraint keywords: NOT NULL, NULL, DEFAULT, PRIMARY KEY, REFERENCES, UNIQUE, CHECK, AUTO_INCREMENT, AUTOINCREMENT, IDENTITY, GENERATED
        type_end = re.search(
            r"\b(NOT\s+NULL|NULL|DEFAULT|PRIMARY\s+KEY|REFERENCES|UNIQUE|CHECK|AUTO_INCREMENT|AUTOINCREMENT|IDENTITY|GENERATED)\b",
            remainder,
            re.IGNORECASE,
        )
        if type_end:
            col_type = _normalise_type(remainder[: type_end.start()])
            constraint_str = remainder[type_end.start() :]
        else:
            col_type = _normalise_type(remainder)
            constraint_str = ""

        not_null = bool(re.search(r"\bNOT\s+NULL\b", constraint_str, re.IGNORECASE))
        pk = bool(re.search(r"\bPRIMARY\s+KEY\b", constraint_str, re.IGNORECASE))

        default_val: Optional[str] = None
        default_match = re.search(
            r"\bDEFAULT\s+(\([^)]+\)|'[^']*'|\"[^\"]*\"|\S+)",
            constraint_str,
            re.IGNORECASE,
        )
        if default_match:
            default_val = default_match.group(1).strip()

        columns.append(
            {
                "name": col_name,
                "type": col_type,
                "not_null": not_null,
                "default": default_val,
                "pk": pk,
            }
        )

    return {
        "table_name": table_name,
        "columns": columns,
        "constraints": constraints,
    }


def _build_table_ir(info: dict) -> tuple[Table, list[Paragraph]]:
    """
    Build an IR Table from the parsed CREATE TABLE info dict,
    plus Paragraph nodes for any standalone constraints.
    """
    header_row = TableRow(
        cells=[
            TableCell(content=[TextRun(text="Column", bold=True)], is_header=True),
            TableCell(content=[TextRun(text="Type", bold=True)], is_header=True),
            TableCell(content=[TextRun(text="NOT NULL", bold=True)], is_header=True),
            TableCell(content=[TextRun(text="Default", bold=True)], is_header=True),
            TableCell(content=[TextRun(text="PK", bold=True)], is_header=True),
        ]
    )

    data_rows: list[TableRow] = []
    for col in info["columns"]:
        data_rows.append(
            TableRow(
                cells=[
                    TableCell(content=[TextRun(text=col["name"], code=True)]),
                    TableCell(content=[TextRun(text=col["type"])]),
                    TableCell(
                        content=[TextRun(text="YES" if col["not_null"] else "")]
                    ),
                    TableCell(
                        content=[TextRun(text=col["default"] or "")]
                    ),
                    TableCell(
                        content=[TextRun(text="YES" if col["pk"] else "")]
                    ),
                ]
            )
        )

    table = Table(rows=[header_row] + data_rows)

    constraint_paragraphs: list[Paragraph] = []
    for c in info["constraints"]:
        constraint_paragraphs.append(
            Paragraph(runs=[TextRun(text=c, bold=True)])
        )

    return table, constraint_paragraphs


# ── Parser ───────────────────────────────────────────────────────────────────


@registry.register
class SQLParser(Parser):
    """
    Parses .sql files into an IR Document.

    CREATE TABLE statements are rendered as structured Table nodes.
    CREATE VIEW / CREATE PROCEDURE / CREATE FUNCTION are rendered as
    headed Sections with CodeBlock bodies.
    CREATE INDEX is rendered under the table it references when possible.
    All other statements become CodeBlock nodes.
    """

    extensions = [".sql"]
    mime_types = ["application/sql"]
    requires = ["sqlparse"]

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        import sqlparse

        options = options or ParseOptions()
        collector = getattr(options, "collector", None)

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

        sections: list[Section] = []
        word_count = 0
        # Track table sections by normalised name for CREATE INDEX grouping
        table_sections: dict[str, Section] = {}

        try:
            statements = sqlparse.split(raw)
        except Exception as exc:
            log.debug("sqlparse.split failed: %s", exc)
            if collector is not None:
                collector.add(
                    ConversionWarning(
                        type=WarningType.CONTENT_EXTRACTED,
                        message=f"SQL parse failed: {exc}; rendered as raw code block.",
                    )
                )
            sections.append(
                Section(
                    level=1,
                    heading=None,
                    blocks=[CodeBlock(code=raw, language="sql")],
                )
            )
            return Document(
                metadata=DocumentMetadata(
                    source_format="sql", word_count=_count_words(raw)
                ),
                sections=sections,
            )

        for stmt_text in statements:
            stmt_text = stmt_text.strip()
            if not stmt_text:
                continue

            # Extract leading comments for description
            leading_comment = _extract_leading_comments(stmt_text)

            try:
                parsed_list = sqlparse.parse(stmt_text)
                if not parsed_list:
                    continue
                stmt = parsed_list[0]
                stmt_type = (stmt.get_type() or "").upper()
            except Exception as exc:
                log.debug("sqlparse.parse failed for statement: %s", exc)
                if collector is not None:
                    collector.add(
                        ConversionWarning(
                            type=WarningType.CONTENT_EXTRACTED,
                            message=f"SQL statement parse error: {exc}; rendered as raw code block.",
                        )
                    )
                sections.append(
                    Section(
                        level=1,
                        heading=None,
                        blocks=[CodeBlock(code=stmt_text, language="sql")],
                    )
                )
                continue

            if stmt_type == "CREATE":
                self._handle_create(
                    stmt_text,
                    stmt,
                    leading_comment,
                    sections,
                    table_sections,
                    collector,
                )
                # Word count: table/column names + comment text
                upper_text = stmt_text.upper()
                if "CREATE TABLE" in upper_text:
                    info = _parse_create_table(stmt)
                    if info:
                        word_count += _count_words(
                            info["table_name"],
                            *(c["name"] for c in info["columns"]),
                        )
                if leading_comment:
                    word_count += _count_words(leading_comment)
            else:
                # Non-CREATE: render as CodeBlock
                blocks: list = []
                if leading_comment:
                    blocks.append(
                        Paragraph(runs=[TextRun(text=leading_comment)])
                    )
                    word_count += _count_words(leading_comment)
                blocks.append(CodeBlock(code=stmt_text, language="sql"))
                sections.append(
                    Section(level=1, heading=None, blocks=blocks)
                )

        return Document(
            metadata=DocumentMetadata(
                source_format="sql",
                word_count=word_count if word_count > 0 else None,
            ),
            sections=sections,
        )

    # ── CREATE dispatching ───────────────────────────────────────────────

    def _handle_create(
        self,
        stmt_text: str,
        stmt,
        leading_comment: str,
        sections: list[Section],
        table_sections: dict[str, Section],
        collector,
    ) -> None:
        """Dispatch a CREATE statement to the appropriate handler."""
        upper = stmt_text.upper()

        if re.search(r"\bCREATE\s+(OR\s+REPLACE\s+)?TABLE\b", upper):
            self._handle_create_table(
                stmt_text, stmt, leading_comment, sections, table_sections, collector
            )
        elif re.search(r"\bCREATE\s+(OR\s+REPLACE\s+)?VIEW\b", upper):
            self._handle_create_view(stmt_text, leading_comment, sections)
        elif re.search(
            r"\bCREATE\s+(OR\s+REPLACE\s+)?(PROCEDURE|FUNCTION)\b", upper
        ):
            self._handle_create_routine(stmt_text, leading_comment, sections)
        elif re.search(r"\bCREATE\s+(UNIQUE\s+)?INDEX\b", upper):
            self._handle_create_index(
                stmt_text, leading_comment, sections, table_sections
            )
        else:
            # Unknown CREATE variant — render as code block
            blocks: list = []
            if leading_comment:
                blocks.append(Paragraph(runs=[TextRun(text=leading_comment)]))
            blocks.append(CodeBlock(code=stmt_text, language="sql"))
            sections.append(Section(level=1, heading=None, blocks=blocks))

    def _handle_create_table(
        self,
        stmt_text: str,
        stmt,
        leading_comment: str,
        sections: list[Section],
        table_sections: dict[str, Section],
        collector,
    ) -> None:
        """Parse CREATE TABLE into a structured Table IR node."""
        try:
            info = _parse_create_table(stmt)
        except Exception as exc:
            log.debug("CREATE TABLE extraction failed: %s", exc)
            info = None

        if info is None:
            # Fallback: emit warning and render as code block
            if collector is not None:
                collector.add(
                    ConversionWarning(
                        type=WarningType.CONTENT_EXTRACTED,
                        message="CREATE TABLE structure extraction failed; rendered as raw code block.",
                    )
                )
            blocks: list = []
            if leading_comment:
                blocks.append(Paragraph(runs=[TextRun(text=leading_comment)]))
            blocks.append(CodeBlock(code=stmt_text, language="sql"))
            sections.append(Section(level=1, heading=None, blocks=blocks))
            return

        table_name = info["table_name"]
        heading_runs = [TextRun(text=f"Table: {table_name}")]
        blocks = []

        if leading_comment:
            blocks.append(Paragraph(runs=[TextRun(text=leading_comment)]))

        table_ir, constraint_paragraphs = _build_table_ir(info)
        blocks.append(table_ir)
        blocks.extend(constraint_paragraphs)

        sec = Section(level=1, heading=heading_runs, blocks=blocks)
        sections.append(sec)
        table_sections[table_name.upper()] = sec

    def _handle_create_view(
        self,
        stmt_text: str,
        leading_comment: str,
        sections: list[Section],
    ) -> None:
        """Parse CREATE VIEW into a Section with CodeBlock body."""
        name_match = re.search(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+[`\"\[]?(\w+(?:\.\w+)?)[`\"\]]?",
            stmt_text,
            re.IGNORECASE,
        )
        name = name_match.group(1) if name_match else "view"

        blocks: list = []
        if leading_comment:
            blocks.append(Paragraph(runs=[TextRun(text=leading_comment)]))
        blocks.append(CodeBlock(code=stmt_text, language="sql"))

        sections.append(
            Section(
                level=1,
                heading=[TextRun(text=f"View: {name}")],
                blocks=blocks,
            )
        )

    def _handle_create_routine(
        self,
        stmt_text: str,
        leading_comment: str,
        sections: list[Section],
    ) -> None:
        """Parse CREATE PROCEDURE/FUNCTION into a Section with CodeBlock body."""
        name_match = re.search(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\s+[`\"\[]?(\w+(?:\.\w+)?)[`\"\]]?",
            stmt_text,
            re.IGNORECASE,
        )
        kind_match = re.search(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?(PROCEDURE|FUNCTION)\b",
            stmt_text,
            re.IGNORECASE,
        )
        name = name_match.group(1) if name_match else "routine"
        kind = kind_match.group(1).capitalize() if kind_match else "Routine"

        blocks: list = []
        if leading_comment:
            blocks.append(Paragraph(runs=[TextRun(text=leading_comment)]))
        blocks.append(CodeBlock(code=stmt_text, language="sql"))

        sections.append(
            Section(
                level=1,
                heading=[TextRun(text=f"{kind}: {name}")],
                blocks=blocks,
            )
        )

    def _handle_create_index(
        self,
        stmt_text: str,
        leading_comment: str,
        sections: list[Section],
        table_sections: dict[str, Section],
    ) -> None:
        """
        Parse CREATE INDEX. If the table it references has already been
        processed, add as a level-2 subsection under that table. Otherwise,
        add as a standalone level-1 section.
        """
        # Extract index name and table name
        idx_match = re.search(
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+(?:\.\w+)?)[`\"\]]?\s+ON\s+[`\"\[]?(\w+(?:\.\w+)?)[`\"\]]?",
            stmt_text,
            re.IGNORECASE,
        )

        blocks: list = []
        if leading_comment:
            blocks.append(Paragraph(runs=[TextRun(text=leading_comment)]))
        blocks.append(CodeBlock(code=stmt_text, language="sql"))

        if idx_match:
            idx_name = idx_match.group(1)
            tbl_name = idx_match.group(2)

            parent_section = table_sections.get(tbl_name.upper())
            if parent_section is not None:
                parent_section.subsections.append(
                    Section(
                        level=2,
                        heading=[TextRun(text=f"Index: {idx_name}")],
                        blocks=blocks,
                    )
                )
                return

            # Table not yet seen — standalone section
            sections.append(
                Section(
                    level=1,
                    heading=[TextRun(text=f"Index: {idx_name}")],
                    blocks=blocks,
                )
            )
        else:
            # Could not parse index — render as plain section
            sections.append(
                Section(level=1, heading=None, blocks=blocks)
            )

"""
distill.parsers.docx
~~~~~~~~~~~~~~~~~~~~
Parser for Microsoft Word documents (.docx, .doc).

Primary path:   mammoth  (.docx → clean HTML → IR)
Metadata path:  python-docx (document properties)
Legacy path:    LibreOffice headless (.doc → .docx pre-conversion)
Fallback:       pandoc  (complex documents, tracked changes)

Install:
    pip install distill-core           # includes mammoth, python-docx
    pip install distill-core[legacy]   # adds LibreOffice support for .doc
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    CodeBlock, Document, DocumentMetadata, Image, ImageType,
    List, ListItem, Paragraph, Section, Table, TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseError, ParseOptions, Parser, UnsupportedFormatError
from distill.registry import registry


@registry.register
class DocxParser(Parser):
    """
    Parses .docx files using mammoth for content and python-docx for metadata.
    """

    extensions = [".docx"]
    mime_types = [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]
    requires          = ["mammoth", "docx"]   # mammoth + python-docx (import name: docx)
    optional_requires = ["pandoc"]

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()

        try:
            import mammoth
            import docx as python_docx
        except ImportError as e:
            raise ParseError(f"Missing dependency: {e}") from e

        path = Path(source) if not isinstance(source, bytes) else None

        # ── Metadata via python-docx ─────────────────────────────────────────
        metadata = DocumentMetadata(source_format="docx")
        if path:
            try:
                doc_obj   = python_docx.Document(str(path))
                core_props = doc_obj.core_properties
                metadata.title       = core_props.title or None
                metadata.author      = core_props.author or None
                metadata.created_at  = core_props.created.isoformat() if core_props.created else None
                metadata.modified_at = core_props.modified.isoformat() if core_props.modified else None
                metadata.source_path = str(path)
                # Estimate word count from paragraphs
                metadata.word_count = sum(
                    len(p.text.split()) for p in doc_obj.paragraphs
                )
            except Exception:
                pass  # metadata extraction is best-effort

        # ── Content via mammoth ──────────────────────────────────────────────
        try:
            if path:
                with open(path, "rb") as f:
                    result = mammoth.convert_to_html(f)
            else:
                import io
                result = mammoth.convert_to_html(io.BytesIO(source))
        except Exception as e:
            raise ParseError(f"mammoth failed to parse document: {e}") from e

        html   = result.value
        document = Document(metadata=metadata)

        # Warn about mammoth messages
        for msg in result.messages:
            document.warnings.append(f"[docx] {msg}")

        # ── Parse HTML → IR ──────────────────────────────────────────────────
        # TODO: implement _html_to_ir(html) → list[Section]
        # Interim: create a single section with a paragraph containing the raw text
        import re
        plain = re.sub(r"<[^>]+>", " ", html).strip()
        plain = re.sub(r"\s+", " ", plain)
        section = Section(level=0, blocks=[Paragraph(runs=[TextRun(text=plain)])])
        document.sections.append(section)
        document.warnings.append(
            "[docx] HTML-to-IR conversion is not yet implemented; raw text used as fallback"
        )

        return document


@registry.register
class DocLegacyParser(Parser):
    """
    Converts legacy .doc files to .docx via LibreOffice, then delegates to DocxParser.
    """

    extensions            = [".doc"]
    mime_types            = ["application/msword"]
    requires              = ["mammoth", "docx"]
    requires_libreoffice  = True

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        # TODO: implement LibreOffice .doc → .docx conversion
        # then call DocxParser().parse(converted_path, options)
        raise ParseError(".doc conversion via LibreOffice is not yet implemented")

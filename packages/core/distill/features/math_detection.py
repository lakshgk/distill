"""
distill.features.math_detection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Detect mathematical content in PDF and DOCX documents.

PDF:  scans character font names and Unicode ranges for math signals.
DOCX: walks the underlying XML for OMML <m:oMath> elements.
"""

from __future__ import annotations

import logging
from typing import Optional

from distill.warnings import ConversionWarning, WarningCollector, WarningType

_logger = logging.getLogger(__name__)

# Known math font prefixes (case-insensitive match)
_MATH_FONT_PREFIXES = ("cmmi", "cmsy", "msam", "msbm", "mtsy", "rmtmi")

# Unicode math ranges
_MATH_RANGES = [
    (0x2200, 0x22FF),   # Mathematical Operators
    (0x1D400, 0x1D7FF), # Mathematical Alphanumeric Symbols
    (0x2100, 0x214F),   # Letterlike Symbols
]


def _is_math_char(ch: str) -> bool:
    """Return True if *ch* falls in a known math Unicode range."""
    cp = ord(ch) if ch else 0
    return any(lo <= cp <= hi for lo, hi in _MATH_RANGES)


class MathDetector:
    """Detect mathematical content in documents."""

    def detect_in_pdf(
        self,
        page_data: list[dict],
        collector: WarningCollector,
    ) -> None:
        """Scan pdfplumber character data for math fonts and Unicode math symbols.

        Parameters
        ----------
        page_data:
            List of character dicts, each with at least ``fontname``, ``text``,
            and ``page_number`` keys.
        collector:
            Warning collector to emit ``MATH_DETECTED`` warning if found.

        Never raises.
        """
        try:
            self._detect_in_pdf_impl(page_data, collector)
        except Exception as exc:
            _logger.debug("MathDetector.detect_in_pdf error: %s", exc)

    def detect_in_docx(
        self,
        docx_path: str,
        collector: WarningCollector,
    ) -> bool:
        """Check a DOCX file for OMML math elements.

        Returns ``True`` if any ``<m:oMath>`` elements are found. Emits a
        ``MATH_CONVERSION_PARTIAL`` warning when math is detected.

        Never raises. Returns ``False`` on any error.
        """
        try:
            return self._detect_in_docx_impl(docx_path, collector)
        except Exception as exc:
            _logger.debug("MathDetector.detect_in_docx error: %s", exc)
            return False

    # ── Internal ─────────────────────────────────────────────────────────────

    def _detect_in_pdf_impl(
        self,
        page_data: list[dict],
        collector: WarningCollector,
    ) -> None:
        math_pages: set[int] = set()

        for char in page_data:
            fontname = (char.get("fontname") or "").lower()
            text = char.get("text") or ""
            page_num = char.get("page_number")

            is_math = False

            # Check font prefix
            if any(fontname.startswith(prefix) for prefix in _MATH_FONT_PREFIXES):
                is_math = True

            # Check Unicode math ranges
            if not is_math and text:
                for ch in text:
                    if _is_math_char(ch):
                        is_math = True
                        break

            if is_math and page_num is not None:
                math_pages.add(page_num)

        if math_pages:
            sorted_pages = sorted(math_pages)
            collector.add(ConversionWarning(
                type=WarningType.MATH_DETECTED,
                message=(
                    f"Math fonts or Unicode math symbols detected on "
                    f"{len(sorted_pages)} page(s). Math conversion is not "
                    f"currently enabled; equations may appear as garbled text."
                ),
                pages=sorted_pages,
                count=len(sorted_pages),
            ))

    def _detect_in_docx_impl(
        self,
        docx_path: str,
        collector: WarningCollector,
    ) -> bool:
        import zipfile
        import defusedxml.ElementTree as ET

        _MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"

        with zipfile.ZipFile(docx_path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return False
            with zf.open("word/document.xml") as f:
                tree = ET.parse(f)

        root = tree.getroot()
        math_elements = root.iter(f"{{{_MATH_NS}}}oMath")

        # iter is lazy — check if at least one exists
        has_math = False
        for _ in math_elements:
            has_math = True
            break

        if has_math:
            collector.add(ConversionWarning(
                type=WarningType.MATH_CONVERSION_PARTIAL,
                message=(
                    "OMML math elements detected in this document. Complex "
                    "equation structures may not have converted cleanly."
                ),
            ))

        return has_math

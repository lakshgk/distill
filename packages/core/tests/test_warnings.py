"""
Tests for distill.warnings — WarningCollector and ConversionWarning.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pytest

from distill.warnings import ConversionWarning, WarningCollector, WarningType


# ── WarningCollector unit tests ──────────────────────────────────────────────

def test_add_and_all_accumulate():
    c = WarningCollector()
    c.add(ConversionWarning(type=WarningType.MATH_DETECTED, message="found math"))
    c.add(ConversionWarning(type=WarningType.TABLE_TRUNCATED, message="rows capped"))
    assert len(c.all()) == 2


def test_all_returns_copy():
    c = WarningCollector()
    c.add(ConversionWarning(type=WarningType.SCANNED_CONTENT, message="scanned"))
    snapshot = c.all()
    c.add(ConversionWarning(type=WarningType.TABLE_TRUNCATED, message="truncated"))
    assert len(snapshot) == 1  # original snapshot unaffected


def test_has_returns_true_when_present():
    c = WarningCollector()
    c.add(ConversionWarning(type=WarningType.MATH_DETECTED, message="math"))
    assert c.has(WarningType.MATH_DETECTED) is True


def test_has_returns_false_when_absent():
    c = WarningCollector()
    c.add(ConversionWarning(type=WarningType.MATH_DETECTED, message="math"))
    assert c.has(WarningType.SCANNED_CONTENT) is False


def test_has_on_empty_collector():
    c = WarningCollector()
    assert c.has(WarningType.TABLE_TRUNCATED) is False


# ── to_dict serialisation ────────────────────────────────────────────────────

def test_to_dict_omits_none_optional_fields():
    c = WarningCollector()
    c.add(ConversionWarning(type=WarningType.CROSS_PAGE_TABLE, message="spans page"))
    d = c.to_dict()
    assert len(d) == 1
    assert "pages" not in d[0]
    assert "count" not in d[0]


def test_to_dict_includes_pages_and_count_when_set():
    c = WarningCollector()
    c.add(ConversionWarning(
        type=WarningType.MATH_DETECTED,
        message="14 equations",
        count=14,
        pages=[2, 3, 7],
    ))
    d = c.to_dict()
    assert d[0]["count"] == 14
    assert d[0]["pages"] == [2, 3, 7]


def test_to_dict_type_is_string_value():
    c = WarningCollector()
    c.add(ConversionWarning(type=WarningType.AUDIO_QUALITY_LOW, message="low bitrate"))
    d = c.to_dict()
    assert d[0]["type"] == "audio_quality_low"


def test_to_dict_on_empty_collector_returns_empty_list():
    c = WarningCollector()
    assert c.to_dict() == []


def test_to_dict_preserves_order():
    c = WarningCollector()
    c.add(ConversionWarning(type=WarningType.MATH_DETECTED, message="first"))
    c.add(ConversionWarning(type=WarningType.TABLE_TRUNCATED, message="second"))
    d = c.to_dict()
    assert d[0]["type"] == "math_detected"
    assert d[1]["type"] == "table_truncated"


# ── Integration: convert() returns structured_warnings ──────────────────────

def _make_docx_bytes() -> bytes:
    import docx as _docx
    doc = _docx.Document()
    doc.add_heading("Test", level=1)
    doc.add_paragraph("Body text.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_convert_result_has_structured_warnings_field():
    from distill import convert

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(_make_docx_bytes())
        tmp = Path(f.name)

    try:
        result = convert(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    assert hasattr(result, "structured_warnings")
    assert isinstance(result.structured_warnings, list)


def test_convert_clean_document_returns_empty_structured_warnings():
    from distill import convert

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(_make_docx_bytes())
        tmp = Path(f.name)

    try:
        result = convert(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    assert result.structured_warnings == []

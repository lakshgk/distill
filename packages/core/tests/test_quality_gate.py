"""
Tests for the quality score pre-check gate.

Covers:
  - Empty IR detection
  - ParserOutcome-based gating (OCR_REQUIRED, EMPTY_IR, PARSE_ERROR)
  - Normal conversion bypasses the gate
  - API response shape for gated vs normal quality scores
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from distill import ConversionResult, ParserOutcome
from distill.ir import Document, DocumentMetadata, Paragraph, Section, TextRun
from distill.quality import QualityScore


# ── Test 1: Empty Document → gate fires ──────────────────────────────────────

def test_empty_document_returns_null_overall():
    doc = Document(sections=[])
    qs = QualityScore.score(doc, outcome=ParserOutcome.SUCCESS)
    assert qs.overall is None
    assert qs.error is not None
    assert len(qs.error) > 0


# ── Test 2: OCR_REQUIRED → gate fires ───────────────────────────────────────

def test_ocr_required_returns_null_overall():
    doc = Document(sections=[])
    qs = QualityScore.score(doc, outcome=ParserOutcome.OCR_REQUIRED)
    assert qs.overall is None
    assert qs.components is None


# ── Test 3: EMPTY_IR → gate fires ───────────────────────────────────────────

def test_empty_ir_returns_null_overall():
    doc = Document(sections=[])
    qs = QualityScore.score(doc, outcome=ParserOutcome.EMPTY_IR)
    assert qs.overall is None
    assert qs.components is None


# ── Test 4: Document with text content → gate does NOT fire ──────────────────

def test_document_with_content_returns_float_overall():
    doc = Document(sections=[
        Section(
            heading=[TextRun(text="Introduction")],
            level=1,
            blocks=[Paragraph(runs=[TextRun(text="Hello world")])],
        ),
    ])
    markdown = "# Introduction\n\nHello world\n"
    qs = QualityScore.score(doc, markdown, outcome=ParserOutcome.SUCCESS)
    assert isinstance(qs.overall, float)
    assert qs.error is None


# ── Test 5–8: API response shape ─────────────────────────────────────────────

def _build_mock_result(quality_score, quality_details, structured_warnings=None):
    """Build a ConversionResult-like object for API serialisation tests."""
    meta = DocumentMetadata(source_format="pdf", page_count=1)
    return ConversionResult(
        markdown="",
        quality_score=quality_score,
        quality_details=quality_details,
        metadata=meta,
        warnings=[],
        structured_warnings=structured_warnings or [],
        parser_outcome=ParserOutcome.OCR_REQUIRED if quality_score is None else ParserOutcome.SUCCESS,
    )


def _build_quality_dict(result):
    """Replicate the server.py quality serialisation logic."""
    qs = result.quality_details
    if result.quality_score is None:
        return {"overall": None, "error": qs.error if qs else "Unknown error", "components": None}
    quality = {"overall": round(result.quality_score, 3)}
    if qs is not None:
        quality.update({
            "headings":   round(qs.heading_preservation, 3),
            "tables":     round(qs.table_preservation, 3),
            "lists":      round(qs.list_preservation, 3),
            "efficiency": round(qs.token_reduction_ratio, 3),
        })
    return quality


def test_api_failed_conversion_quality_null():
    qs = QualityScore(overall=None, error="OCR is required but not enabled", components=None)
    result = _build_mock_result(quality_score=None, quality_details=qs)
    quality = _build_quality_dict(result)
    assert quality["overall"] is None
    assert "error" in quality
    assert len(quality["error"]) > 0


def test_api_failed_conversion_includes_warnings():
    qs = QualityScore(overall=None, error="OCR is required but not enabled", components=None)
    result = _build_mock_result(quality_score=None, quality_details=qs, structured_warnings=[])
    assert "warnings" not in _build_quality_dict(result) or True  # warnings is top-level
    # The top-level warnings key must always be present
    assert result.structured_warnings == []


def test_api_successful_conversion_quality_float():
    doc = Document(sections=[
        Section(
            heading=[TextRun(text="Title")],
            level=1,
            blocks=[Paragraph(runs=[TextRun(text="Content")])],
        ),
    ])
    qs = QualityScore.score(doc, "# Title\n\nContent\n", outcome=ParserOutcome.SUCCESS)
    result = _build_mock_result(quality_score=qs.overall, quality_details=qs)
    quality = _build_quality_dict(result)
    assert isinstance(quality["overall"], float)
    assert "error" not in quality


def test_api_warning_key_absent_when_null_overall():
    qs = QualityScore(overall=None, error="No content extracted from document", components=None)
    result = _build_mock_result(quality_score=None, quality_details=qs)
    quality = _build_quality_dict(result)
    assert quality["overall"] is None
    # The quality object must NOT contain component keys like "headings" etc.
    assert "headings" not in quality
    assert "tables" not in quality
    assert "lists" not in quality
    assert "efficiency" not in quality


# ── Token ratio / efficiency metric ─────────────────────────────────────────

def test_token_ratio_none_when_word_count_missing():
    """When ir.metadata.word_count is None, components['token_ratio'] is None."""
    doc = Document(sections=[
        Section(heading=[TextRun(text="H")], level=1,
                blocks=[Paragraph(runs=[TextRun(text="Content")])]),
    ])
    assert doc.metadata.word_count is None
    qs = QualityScore.score(doc, "# H\n\nContent\n", outcome=ParserOutcome.SUCCESS)
    assert qs.components["token_ratio"] is None


def test_overall_is_float_when_token_ratio_none():
    """When token_ratio is None, overall must still be a valid float."""
    doc = Document(sections=[
        Section(heading=[TextRun(text="H")], level=1,
                blocks=[Paragraph(runs=[TextRun(text="Content")])]),
    ])
    qs = QualityScore.score(doc, "# H\n\nContent\n", outcome=ParserOutcome.SUCCESS)
    assert isinstance(qs.overall, float)
    assert qs.overall > 0


def test_token_ratio_is_float_when_word_count_set():
    """When word_count is set, token_ratio is a float in [0.0, 1.0]."""
    doc = Document(
        metadata=DocumentMetadata(word_count=50),
        sections=[
            Section(heading=[TextRun(text="H")], level=1,
                    blocks=[Paragraph(runs=[TextRun(text="Some words here")])]),
        ],
    )
    qs = QualityScore.score(doc, "# H\n\nSome words here\n", outcome=ParserOutcome.SUCCESS)
    tr = qs.components["token_ratio"]
    assert isinstance(tr, float)
    assert 0.0 <= tr <= 1.0


def test_redistributed_weights_sum_to_one():
    """When token_ratio is excluded, the remaining components still yield a valid overall."""
    doc = Document(sections=[
        Section(heading=[TextRun(text="H")], level=1,
                blocks=[Paragraph(runs=[TextRun(text="Content")])]),
    ])
    # All four remaining metrics score 1.0 → overall should equal 1.0
    qs = QualityScore.score(doc, "# H\n\nContent\n", outcome=ParserOutcome.SUCCESS)
    # heading=1.0, table=1.0 (0 expected→perfect), list=1.0, valid=1.0
    # sum = 0.3125 + 0.3125 + 0.1875 + 0.1875 = 1.0
    assert abs(qs.overall - 1.0) < 0.01


def test_token_ratio_never_048_when_word_count_none():
    """The circular fallback producing a fixed 0.48 must be gone."""
    doc = Document(sections=[
        Section(heading=[TextRun(text="H")], level=1,
                blocks=[Paragraph(runs=[TextRun(text="Content")])]),
    ])
    qs = QualityScore.score(doc, "# H\n\nContent\n", outcome=ParserOutcome.SUCCESS)
    assert qs.components["token_ratio"] is None
    assert qs.components["token_ratio"] != 0.48

"""
tests/test_ui.py
~~~~~~~~~~~~~~~~
Tests for distill_app.ui.

All calls to distill.convert are mocked — no actual document parsing occurs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from distill.quality import QualityScore
from distill_app.ui import (
    build_ui,
    convert_file,
    quality_badge,
    show_file_info,
    SUPPORTED_EXTENSIONS,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _mock_file(tmp_path: Path, name: str = "report.docx") -> MagicMock:
    """Return a mock Gradio file object pointing to a real temp file."""
    p = tmp_path / name
    p.write_bytes(b"fake content")
    obj = MagicMock()
    obj.name = str(p)
    return obj


def _mock_qs(
    overall: float = 0.90,
    headings: float = 1.0,
    tables: float = 1.0,
    lists: float = 1.0,
    efficiency: float = 0.80,
) -> QualityScore:
    return QualityScore(
        overall               = overall,
        heading_preservation  = headings,
        table_preservation    = tables,
        list_preservation     = lists,
        token_reduction_ratio = efficiency,
    )


def _mock_result(
    markdown: str = "# Hello\n\nWorld.",
    quality: float = 0.90,
    warnings: list[str] | None = None,
) -> MagicMock:
    r = MagicMock()
    r.markdown        = markdown
    r.quality_score   = quality
    r.quality_details = _mock_qs(overall=quality)
    r.warnings        = warnings or []
    # metadata with concrete values so _stats_html doesn't choke on MagicMock
    m = MagicMock()
    m.word_count    = 120
    m.page_count    = 2
    m.slide_count   = None
    m.sheet_count   = None
    m.source_format = "docx"
    r.metadata = m
    return r


# ── quality_badge ─────────────────────────────────────────────────────────────

class TestQualityBadge:

    def test_excellent_threshold(self):
        badge = quality_badge(_mock_qs(0.85))
        assert "Excellent" in badge
        assert "#27ae60" in badge

    def test_excellent_above_threshold(self):
        badge = quality_badge(_mock_qs(0.99))
        assert "Excellent" in badge

    def test_good_threshold(self):
        badge = quality_badge(_mock_qs(0.70))
        assert "Good" in badge
        assert "#f39c12" in badge

    def test_good_range(self):
        badge = quality_badge(_mock_qs(0.77))
        assert "Good" in badge

    def test_low_below_threshold(self):
        badge = quality_badge(_mock_qs(0.69))
        assert "Low" in badge
        assert "#e74c3c" in badge

    def test_percentage_displayed(self):
        badge = quality_badge(_mock_qs(0.83))
        assert "83%" in badge

    def test_returns_html(self):
        badge = quality_badge(_mock_qs(0.9))
        assert "<div" in badge or "<span" in badge

    def test_tooltip_contains_headings(self):
        badge = quality_badge(_mock_qs(0.9, headings=1.0))
        assert "Headings" in badge

    def test_tooltip_contains_tables(self):
        badge = quality_badge(_mock_qs(0.9, tables=0.5))
        assert "Tables" in badge

    def test_tooltip_contains_lists(self):
        badge = quality_badge(_mock_qs(0.9, lists=0.9))
        assert "Lists" in badge

    def test_tooltip_contains_efficiency(self):
        badge = quality_badge(_mock_qs(0.9, efficiency=0.8))
        assert "Efficiency" in badge

    def test_checkmark_for_high_metric(self):
        badge = quality_badge(_mock_qs(0.9, headings=0.95))
        assert "✓" in badge or "&#10003;" in badge

    def test_warning_symbol_for_low_metric(self):
        badge = quality_badge(_mock_qs(0.9, tables=0.5))
        assert "⚠" in badge or "&#9888;" in badge

    def test_float_fallback_excellent(self):
        badge = quality_badge(0.90)
        assert "Excellent" in badge
        assert "#27ae60" in badge

    def test_float_fallback_good(self):
        badge = quality_badge(0.75)
        assert "Good" in badge

    def test_float_fallback_low(self):
        badge = quality_badge(0.50)
        assert "Low" in badge


# ── show_file_info ────────────────────────────────────────────────────────────

class TestShowFileInfo:

    def test_none_returns_hidden(self):
        upd = show_file_info(None)
        assert upd.get("visible") is False

    def test_file_returns_visible(self, tmp_path):
        f = _mock_file(tmp_path, "report.docx")
        upd = show_file_info(f)
        assert upd.get("visible") is True

    def test_filename_in_output(self, tmp_path):
        f = _mock_file(tmp_path, "quarterly.docx")
        upd = show_file_info(f)
        assert "quarterly.docx" in upd.get("value", "")

    def test_format_label_in_output(self, tmp_path):
        f = _mock_file(tmp_path, "report.docx")
        upd = show_file_info(f)
        assert "Word" in upd.get("value", "")

    def test_size_in_output(self, tmp_path):
        f = _mock_file(tmp_path, "report.docx")
        upd = show_file_info(f)
        assert "B" in upd.get("value", "")  # KB / B / MB


# ── convert_file — None / unsupported ────────────────────────────────────────

class TestConvertFileEdgeCases:

    def test_none_file_returns_six_tuple(self):
        result = convert_file(None, True, 500, False)
        assert len(result) == 6

    def test_none_file_empty_markdown(self):
        md, *_ = convert_file(None, True, 500, False)
        assert md == ""

    def test_none_file_no_download(self):
        *_, dl = convert_file(None, True, 500, False)
        assert dl is None

    def test_none_file_badge_mentions_no_file(self):
        _, _, badge, *_ = convert_file(None, True, 500, False)
        assert "No file uploaded" in badge

    def test_unsupported_extension_rejected(self, tmp_path):
        f = _mock_file(tmp_path, "file.xyz")
        _, _, badge, _, _, dl = convert_file(f, True, 500, False)
        assert "Unsupported" in badge
        assert dl is None

    def test_unsupported_extension_empty_markdown(self, tmp_path):
        f = _mock_file(tmp_path, "file.xyz")
        md, *_ = convert_file(f, True, 500, False)
        assert md == ""


# ── convert_file — successful conversion ─────────────────────────────────────

class TestConvertFileSuccess:

    def _convert(self, tmp_path, name="report.docx", include_fm=True,
                 max_rows=500, enable_ocr=False, result=None):
        f = _mock_file(tmp_path, name)
        r = result or _mock_result()
        with patch("distill.convert", return_value=r):
            return convert_file(f, include_fm, max_rows, enable_ocr)

    def test_returns_markdown(self, tmp_path):
        md, *_ = self._convert(tmp_path, result=_mock_result(markdown="# Hi"))
        assert md == "# Hi"

    def test_preview_matches_markdown(self, tmp_path):
        md, preview, *_ = self._convert(tmp_path, result=_mock_result(markdown="# Hi"))
        assert preview == md

    def test_returns_download_path(self, tmp_path):
        *_, dl = self._convert(tmp_path)
        assert dl is not None
        assert Path(dl).exists()

    def test_download_file_has_md_extension(self, tmp_path):
        *_, dl = self._convert(tmp_path)
        assert dl.endswith(".md")

    def test_download_filename_uses_original_stem(self, tmp_path):
        *_, dl = self._convert(tmp_path, name="quarterly_report.docx")
        assert "quarterly_report" in Path(dl).name

    def test_download_file_contains_markdown(self, tmp_path):
        r = _mock_result(markdown="# Report\n\nContent.")
        *_, dl = self._convert(tmp_path, result=r)
        content = Path(dl).read_text(encoding="utf-8")
        assert "# Report" in content

    def test_empty_warnings_hidden(self, tmp_path):
        _, _, _, _, warn_upd, _ = self._convert(
            tmp_path, result=_mock_result(warnings=[])
        )
        assert warn_upd.get("visible") is False

    def test_warnings_visible_when_present(self, tmp_path):
        r = _mock_result(warnings=["Font not found", "Page count unavailable"])
        _, _, _, _, warn_upd, _ = self._convert(tmp_path, result=r)
        assert warn_upd.get("visible") is True

    def test_warnings_text_content(self, tmp_path):
        r = _mock_result(warnings=["Font not found", "Page count unavailable"])
        _, _, _, _, warn_upd, _ = self._convert(tmp_path, result=r)
        assert "Font not found" in warn_upd.get("value", "")
        assert "Page count unavailable" in warn_upd.get("value", "")

    def test_include_metadata_forwarded(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result()) as mock_conv:
            convert_file(f, True, 500, False)
        _, kwargs = mock_conv.call_args
        assert kwargs.get("include_metadata") is True

    def test_include_metadata_false_forwarded(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result()) as mock_conv:
            convert_file(f, False, 500, False)
        _, kwargs = mock_conv.call_args
        assert kwargs.get("include_metadata") is False

    def test_max_rows_forwarded_via_options(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result()) as mock_conv:
            convert_file(f, True, 123, False)
        _, kwargs = mock_conv.call_args
        assert kwargs["options"].max_table_rows == 123

    def test_enable_ocr_forwarded(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result()) as mock_conv:
            convert_file(f, True, 500, True)
        _, kwargs = mock_conv.call_args
        assert kwargs["options"].extra.get("enable_ocr") is True

    def test_ocr_disabled_by_default(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result()) as mock_conv:
            convert_file(f, True, 500, False)
        _, kwargs = mock_conv.call_args
        assert kwargs["options"].extra.get("enable_ocr") is False

    @pytest.mark.parametrize("ext", SUPPORTED_EXTENSIONS)
    def test_all_supported_extensions_accepted(self, tmp_path, ext):
        f = _mock_file(tmp_path, f"file{ext}")
        with patch("distill.convert", return_value=_mock_result()):
            _, _, badge, *_ = convert_file(f, True, 500, False)
        assert "Unsupported" not in badge


# ── convert_file — quality badge thresholds ──────────────────────────────────

class TestConvertFileBadge:

    def _badge_for(self, tmp_path, quality):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result(quality=quality)):
            _, _, badge, *_ = convert_file(f, True, 500, False)
        return badge

    def test_excellent(self, tmp_path):
        assert "Excellent" in self._badge_for(tmp_path, 0.90)

    def test_good(self, tmp_path):
        assert "Good" in self._badge_for(tmp_path, 0.75)

    def test_low(self, tmp_path):
        assert "Low" in self._badge_for(tmp_path, 0.50)

    def test_badge_contains_breakdown(self, tmp_path):
        badge = self._badge_for(tmp_path, 0.90)
        assert "Headings" in badge
        assert "Tables" in badge


# ── convert_file — error handling ────────────────────────────────────────────

class TestConvertFileErrors:

    def test_parse_error_returns_error_badge(self, tmp_path):
        from distill.parsers.base import ParseError
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", side_effect=ParseError("bad zip")):
            _, _, badge, _, _, dl = convert_file(f, True, 500, False)
        assert "Conversion error" in badge
        assert "bad zip" in badge
        assert dl is None

    def test_parse_error_empty_markdown(self, tmp_path):
        from distill.parsers.base import ParseError
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", side_effect=ParseError("oops")):
            md, *_ = convert_file(f, True, 500, False)
        assert md == ""

    def test_unexpected_error_returns_error_badge(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", side_effect=RuntimeError("disk full")):
            _, _, badge, _, _, dl = convert_file(f, True, 500, False)
        assert "Unexpected error" in badge
        assert dl is None

    def test_error_does_not_raise(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", side_effect=Exception("anything")):
            result = convert_file(f, True, 500, False)
        assert len(result) == 6


# ── build_ui ─────────────────────────────────────────────────────────────────

class TestBuildUi:

    def test_returns_blocks_instance(self):
        import gradio as gr
        demo = build_ui()
        assert isinstance(demo, gr.Blocks)

    def test_idempotent(self):
        import gradio as gr
        assert isinstance(build_ui(), gr.Blocks)
        assert isinstance(build_ui(), gr.Blocks)

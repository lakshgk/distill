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

from distill_app.ui import build_ui, convert_file, quality_badge, SUPPORTED_EXTENSIONS


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _mock_file(tmp_path: Path, name: str = "report.docx") -> MagicMock:
    """Return a mock Gradio file object pointing to a real temp file."""
    p = tmp_path / name
    p.write_bytes(b"fake content")
    obj = MagicMock()
    obj.name = str(p)
    return obj


def _mock_result(
    markdown: str = "# Hello\n\nWorld.",
    quality: float = 0.90,
    warnings: list[str] | None = None,
) -> MagicMock:
    r = MagicMock()
    r.markdown      = markdown
    r.quality_score = quality
    r.warnings      = warnings or []
    return r


# ── quality_badge ─────────────────────────────────────────────────────────────

class TestQualityBadge:

    def test_excellent_threshold(self):
        badge = quality_badge(0.85)
        assert "Excellent" in badge
        assert "#27ae60" in badge

    def test_excellent_above_threshold(self):
        badge = quality_badge(0.99)
        assert "Excellent" in badge

    def test_good_threshold(self):
        badge = quality_badge(0.70)
        assert "Good" in badge
        assert "#f39c12" in badge

    def test_good_range(self):
        badge = quality_badge(0.77)
        assert "Good" in badge

    def test_low_below_threshold(self):
        badge = quality_badge(0.69)
        assert "Low" in badge
        assert "#e74c3c" in badge

    def test_percentage_displayed(self):
        badge = quality_badge(0.83)
        assert "83%" in badge

    def test_returns_html_span(self):
        badge = quality_badge(0.9)
        assert badge.startswith("<span")
        assert badge.endswith("</span>")


# ── convert_file — None / unsupported ────────────────────────────────────────

class TestConvertFileEdgeCases:

    def test_none_file_returns_four_tuple(self):
        result = convert_file(None, True, 500)
        assert len(result) == 4

    def test_none_file_empty_markdown(self):
        md, _, _, _ = convert_file(None, True, 500)
        assert md == ""

    def test_none_file_no_download(self):
        _, _, _, dl = convert_file(None, True, 500)
        assert dl is None

    def test_none_file_badge_mentions_no_file(self):
        _, badge, _, _ = convert_file(None, True, 500)
        assert "No file uploaded" in badge

    def test_unsupported_extension_rejected(self, tmp_path):
        f = _mock_file(tmp_path, "file.xyz")
        _, badge, _, dl = convert_file(f, True, 500)
        assert "Unsupported" in badge
        assert dl is None

    def test_unsupported_extension_empty_markdown(self, tmp_path):
        f = _mock_file(tmp_path, "file.xyz")
        md, _, _, _ = convert_file(f, True, 500)
        assert md == ""


# ── convert_file — successful conversion ─────────────────────────────────────

class TestConvertFileSuccess:

    def _convert(self, tmp_path, name="report.docx", include_fm=True,
                 max_rows=500, result=None):
        f = _mock_file(tmp_path, name)
        r = result or _mock_result()
        with patch("distill.convert", return_value=r):
            return convert_file(f, include_fm, max_rows)

    def test_returns_markdown(self, tmp_path):
        md, _, _, _ = self._convert(tmp_path, result=_mock_result(markdown="# Hi"))
        assert md == "# Hi"

    def test_returns_download_path(self, tmp_path):
        _, _, _, dl = self._convert(tmp_path)
        assert dl is not None
        assert Path(dl).exists()

    def test_download_file_has_md_extension(self, tmp_path):
        _, _, _, dl = self._convert(tmp_path)
        assert dl.endswith(".md")

    def test_download_filename_uses_original_stem(self, tmp_path):
        _, _, _, dl = self._convert(tmp_path, name="quarterly_report.docx")
        assert "quarterly_report" in Path(dl).name

    def test_download_file_contains_markdown(self, tmp_path):
        r = _mock_result(markdown="# Report\n\nContent.")
        _, _, _, dl = self._convert(tmp_path, result=r)
        content = Path(dl).read_text(encoding="utf-8")
        assert "# Report" in content

    def test_empty_warnings_returns_empty_string(self, tmp_path):
        _, _, warnings, _ = self._convert(tmp_path, result=_mock_result(warnings=[]))
        assert warnings == ""

    def test_warnings_included_in_output(self, tmp_path):
        r = _mock_result(warnings=["Font not found", "Page count unavailable"])
        _, _, warnings, _ = self._convert(tmp_path, result=r)
        assert "Font not found" in warnings
        assert "Page count unavailable" in warnings

    def test_include_metadata_forwarded(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result()) as mock_conv:
            convert_file(f, True, 500)
        _, kwargs = mock_conv.call_args
        assert kwargs.get("include_metadata") is True

    def test_include_metadata_false_forwarded(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result()) as mock_conv:
            convert_file(f, False, 500)
        _, kwargs = mock_conv.call_args
        assert kwargs.get("include_metadata") is False

    def test_max_rows_forwarded_via_options(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result()) as mock_conv:
            convert_file(f, True, 123)
        _, kwargs = mock_conv.call_args
        assert kwargs["options"].max_table_rows == 123

    @pytest.mark.parametrize("ext", SUPPORTED_EXTENSIONS)
    def test_all_supported_extensions_accepted(self, tmp_path, ext):
        f = _mock_file(tmp_path, f"file{ext}")
        with patch("distill.convert", return_value=_mock_result()):
            _, badge, _, _ = convert_file(f, True, 500)
        assert "Unsupported" not in badge


# ── convert_file — quality badge thresholds ──────────────────────────────────

class TestConvertFileBadge:

    def _badge_for(self, tmp_path, quality):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", return_value=_mock_result(quality=quality)):
            _, badge, _, _ = convert_file(f, True, 500)
        return badge

    def test_excellent(self, tmp_path):
        assert "Excellent" in self._badge_for(tmp_path, 0.90)

    def test_good(self, tmp_path):
        assert "Good" in self._badge_for(tmp_path, 0.75)

    def test_low(self, tmp_path):
        assert "Low" in self._badge_for(tmp_path, 0.50)


# ── convert_file — error handling ────────────────────────────────────────────

class TestConvertFileErrors:

    def test_parse_error_returns_error_badge(self, tmp_path):
        from distill.parsers.base import ParseError
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", side_effect=ParseError("bad zip")):
            _, badge, _, dl = convert_file(f, True, 500)
        assert "Conversion error" in badge
        assert "bad zip" in badge
        assert dl is None

    def test_parse_error_empty_markdown(self, tmp_path):
        from distill.parsers.base import ParseError
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", side_effect=ParseError("oops")):
            md, _, _, _ = convert_file(f, True, 500)
        assert md == ""

    def test_unexpected_error_returns_error_badge(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", side_effect=RuntimeError("disk full")):
            _, badge, _, dl = convert_file(f, True, 500)
        assert "Unexpected error" in badge
        assert dl is None

    def test_error_does_not_raise(self, tmp_path):
        f = _mock_file(tmp_path, "doc.docx")
        with patch("distill.convert", side_effect=Exception("anything")):
            result = convert_file(f, True, 500)
        assert len(result) == 4


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

"""
tests/test_server.py
~~~~~~~~~~~~~~~~~~~~
Tests for distill_app.server (FastAPI app).

All calls to distill.convert are mocked — no actual document parsing occurs.
Requires: pip install httpx  (TestClient dependency)
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from distill_app.server import build_app


# ── Shared app / client ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    return TestClient(build_app())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_result(
    markdown: str = "# Hello\n\nWorld.",
    quality: float = 0.90,
    warnings: list[str] | None = None,
) -> MagicMock:
    from distill.quality import QualityScore

    qs = QualityScore(
        overall               = quality,
        heading_preservation  = 1.0,
        table_preservation    = 1.0,
        list_preservation     = 1.0,
        token_reduction_ratio = 0.80,
    )
    r = MagicMock()
    r.markdown             = markdown
    r.quality_score        = quality
    r.quality_details      = qs
    r.warnings             = warnings or []
    r.structured_warnings  = warnings or []
    m = MagicMock()
    m.word_count    = 120
    m.page_count    = 3
    m.slide_count   = None
    m.sheet_count   = None
    m.source_format = "docx"
    r.metadata = m
    return r


def _upload(client, filename="report.docx", content=b"fake", **form_fields):
    """POST /api/convert with a fake uploaded file."""
    data = {"include_metadata": "true", "max_rows": "500", "enable_ocr": "false"}
    data.update(form_fields)
    files = {"file": (filename, io.BytesIO(content), "application/octet-stream")}
    return client.post("/api/convert", data=data, files=files)


# ── GET / ─────────────────────────────────────────────────────────────────────

class TestIndexRoute:

    def test_get_root_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_get_root_content_type_html(self, client):
        resp = client.get("/")
        assert "text/html" in resp.headers["content-type"]

    def test_get_root_body_not_empty(self, client):
        resp = client.get("/")
        assert len(resp.content) > 0


# ── POST /api/convert — unsupported format ────────────────────────────────────

class TestConvertUnsupported:

    def test_unsupported_extension_returns_400(self, client):
        resp = _upload(client, filename="file.xyz")
        assert resp.status_code == 400

    def test_unsupported_extension_detail_mentions_format(self, client):
        resp = _upload(client, filename="file.xyz")
        assert "xyz" in resp.json()["detail"].lower()

    def test_unsupported_extension_detail_lists_supported(self, client):
        resp = _upload(client, filename="file.xyz")
        detail = resp.json()["detail"]
        assert ".docx" in detail or "docx" in detail.lower()

    def test_no_extension_returns_400(self, client):
        resp = _upload(client, filename="noextension")
        assert resp.status_code == 400


# ── POST /api/convert — successful conversion ─────────────────────────────────

class TestConvertSuccess:

    @pytest.fixture(autouse=True)
    def mock_convert(self):
        with patch("distill.convert", return_value=_mock_result()) as m:
            self._mock = m
            yield m

    def test_returns_200(self, client):
        resp = _upload(client, filename="report.docx")
        assert resp.status_code == 200

    def test_response_has_markdown_key(self, client):
        resp = _upload(client, filename="report.docx")
        assert "markdown" in resp.json()

    def test_response_has_quality_key(self, client):
        resp = _upload(client, filename="report.docx")
        assert "quality" in resp.json()

    def test_response_has_stats_key(self, client):
        resp = _upload(client, filename="report.docx")
        assert "stats" in resp.json()

    def test_response_has_warnings_key(self, client):
        resp = _upload(client, filename="report.docx")
        assert "warnings" in resp.json()

    def test_markdown_value_matches_mock(self, client):
        with patch("distill.convert", return_value=_mock_result(markdown="# Test")):
            resp = _upload(client, filename="report.docx")
        assert resp.json()["markdown"] == "# Test"

    def test_quality_overall_present(self, client):
        resp = _upload(client, filename="report.docx")
        assert "overall" in resp.json()["quality"]

    def test_quality_overall_rounded(self, client):
        resp = _upload(client, filename="report.docx")
        overall = resp.json()["quality"]["overall"]
        assert isinstance(overall, float)
        assert overall == round(overall, 3)

    def test_quality_breakdown_keys(self, client):
        resp = _upload(client, filename="report.docx")
        q = resp.json()["quality"]
        for key in ("headings", "tables", "lists", "efficiency"):
            assert key in q, f"missing quality key: {key}"

    def test_stats_word_count(self, client):
        resp = _upload(client, filename="report.docx")
        assert resp.json()["stats"]["words"] == 120

    def test_stats_page_count(self, client):
        resp = _upload(client, filename="report.docx")
        assert resp.json()["stats"]["pages"] == 3

    def test_stats_format_uppercase(self, client):
        resp = _upload(client, filename="report.docx")
        assert resp.json()["stats"]["format"] == "DOCX"

    def test_warnings_empty_list(self, client):
        resp = _upload(client, filename="report.docx")
        assert resp.json()["warnings"] == []

    def test_warnings_propagated(self, client):
        with patch("distill.convert", return_value=_mock_result(warnings=["Font missing"])):
            resp = _upload(client, filename="report.docx")
        assert "Font missing" in resp.json()["warnings"]


# ── POST /api/convert — form fields forwarded ────────────────────────────────

class TestConvertFormFields:

    def test_include_metadata_true_forwarded(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx", include_metadata="true")
        _, kwargs = m.call_args
        assert kwargs["include_metadata"] is True

    def test_include_metadata_false_forwarded(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx", include_metadata="false")
        _, kwargs = m.call_args
        assert kwargs["include_metadata"] is False

    def test_max_rows_forwarded(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx", max_rows="123")
        _, kwargs = m.call_args
        assert kwargs["options"].max_table_rows == 123

    def test_enable_ocr_true_forwarded(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx", enable_ocr="true")
        _, kwargs = m.call_args
        assert kwargs["options"].extra.get("enable_ocr") is True

    def test_enable_ocr_false_forwarded(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx", enable_ocr="false")
        _, kwargs = m.call_args
        assert kwargs["options"].extra.get("enable_ocr") is False


# ── POST /api/convert — all supported formats accepted ───────────────────────

@pytest.mark.parametrize("ext", [".docx", ".doc", ".xlsx", ".xls", ".csv",
                                  ".pptx", ".ppt", ".pdf"])
def test_supported_extension_accepted(client, ext):
    with patch("distill.convert", return_value=_mock_result()):
        resp = _upload(client, filename=f"file{ext}")
    assert resp.status_code == 200


# ── POST /api/convert — error handling ───────────────────────────────────────

class TestConvertErrors:

    def test_parse_error_returns_400(self, client):
        from distill.parsers.base import ParseError
        with patch("distill.convert", side_effect=ParseError("bad zip")):
            resp = _upload(client, filename="doc.docx")
        assert resp.status_code == 400

    def test_parse_error_detail_propagated(self, client):
        from distill.parsers.base import ParseError
        with patch("distill.convert", side_effect=ParseError("corrupt file")):
            resp = _upload(client, filename="doc.docx")
        assert "corrupt file" in resp.json()["detail"]

    def test_unexpected_error_returns_500(self, client):
        with patch("distill.convert", side_effect=RuntimeError("disk full")):
            resp = _upload(client, filename="doc.docx")
        assert resp.status_code == 500

    def test_unexpected_error_detail_mentions_unexpected(self, client):
        with patch("distill.convert", side_effect=RuntimeError("disk full")):
            resp = _upload(client, filename="doc.docx")
        assert "Unexpected error" in resp.json()["detail"]

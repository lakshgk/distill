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


# ── Helpers for the new form field / LLM tests ────────────────────────────────

def _mock_chunks_result():
    """A mock convert() result with two fake chunks for output_format=chunks."""
    r = _mock_result()
    c1 = MagicMock()
    c1.to_dict.return_value = {"chunk_id": "c1", "content": "First chunk",  "token_count": 5}
    c2 = MagicMock()
    c2.to_dict.return_value = {"chunk_id": "c2", "content": "Second chunk", "token_count": 4}
    r.chunks = [c1, c2]
    return r


def _clear_settings(s):
    """Reset every attribute a patched settings MagicMock needs to behave
    like an operator who set no LLM / vision / HF env vars. Without this,
    MagicMock auto-creates truthy attributes which break the `or` fallback
    chains in server.py.
    """
    s.LLM_API_KEY     = ""
    s.LLM_MODEL       = ""
    s.LLM_BASE_URL    = ""
    s.VISION_PROVIDER = ""
    s.VISION_API_KEY  = ""
    s.HF_TOKEN        = ""
    return s


# ── POST /api/convert — LLM configuration ─────────────────────────────────────

class TestLLMConfig:
    """Per-request LLM fields and env var precedence."""

    def test_llm_api_key_per_request_wins_over_env(self, client):
        with patch("distill_app.server.settings") as s, \
             patch("distill.convert", return_value=_mock_result()) as m:
            _clear_settings(s)
            s.LLM_API_KEY = "env-key"
            _upload(client, filename="doc.docx",
                    llm_api_key="request-key",
                    llm_model="gpt-4o")
        opts = m.call_args.kwargs["options"]
        assert opts.llm is not None
        assert opts.llm.api_key == "request-key"

    def test_llm_model_per_request_wins_over_env(self, client):
        with patch("distill_app.server.settings") as s, \
             patch("distill.convert", return_value=_mock_result()) as m:
            _clear_settings(s)
            s.LLM_MODEL = "env-model"
            _upload(client, filename="doc.docx",
                    llm_api_key="sk-test",
                    llm_model="request-model")
        opts = m.call_args.kwargs["options"]
        assert opts.llm.model == "request-model"

    def test_llm_base_url_per_request_wins_over_env(self, client):
        with patch("distill_app.server.settings") as s, \
             patch("distill.convert", return_value=_mock_result()) as m:
            _clear_settings(s)
            s.LLM_BASE_URL = "https://env.example/v1"
            _upload(client, filename="doc.docx",
                    llm_api_key="sk-test",
                    llm_model="gpt-4o",
                    llm_base_url="https://request.example/v1")
        opts = m.call_args.kwargs["options"]
        assert opts.llm.base_url == "https://request.example/v1"

    def test_llm_fields_omitted_fall_back_to_env(self, client):
        with patch("distill_app.server.settings") as s, \
             patch("distill.convert", return_value=_mock_result()) as m:
            _clear_settings(s)
            s.LLM_API_KEY  = "env-key"
            s.LLM_MODEL    = "env-model"
            s.LLM_BASE_URL = "https://env.example/v1"
            _upload(client, filename="doc.docx")
        opts = m.call_args.kwargs["options"]
        assert opts.llm is not None
        assert opts.llm.api_key  == "env-key"
        assert opts.llm.model    == "env-model"
        assert opts.llm.base_url == "https://env.example/v1"

    def test_llm_config_none_when_neither_set(self, client):
        """No key + no model anywhere → options.llm is None."""
        with patch("distill_app.server.settings") as s, \
             patch("distill.convert", return_value=_mock_result()) as m:
            _clear_settings(s)
            _upload(client, filename="doc.docx")
        assert m.call_args.kwargs["options"].llm is None


# ── POST /api/convert — images form field ─────────────────────────────────────

class TestImagesField:
    """Validation and forwarding for the images form field (Path B addition)."""

    def test_images_default_is_extract(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx")
        assert m.call_args.kwargs["options"].images == "extract"

    def test_images_suppress_forwarded(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx", images="suppress")
        assert m.call_args.kwargs["options"].images == "suppress"

    def test_images_inline_ocr_forwarded(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx", images="inline_ocr")
        assert m.call_args.kwargs["options"].images == "inline_ocr"

    def test_images_invalid_value_returns_422(self, client):
        resp = _upload(client, filename="doc.docx", images="bogus")
        assert resp.status_code == 422
        assert "Invalid images mode" in resp.json()["detail"]

    def test_images_caption_without_any_key_returns_422(self, client):
        with patch("distill_app.server.settings") as s:
            _clear_settings(s)
            resp = _upload(client, filename="doc.docx", images="caption")
        assert resp.status_code == 422
        assert "images=caption requires" in resp.json()["detail"]

    def test_images_caption_with_key_reuses_key_for_vision(self, client):
        """Spec decision #4: single key serves all AI features including vision."""
        with patch("distill_app.server.settings") as s, \
             patch("distill.convert", return_value=_mock_result()) as m:
            _clear_settings(s)
            resp = _upload(client, filename="doc.docx",
                           images="caption",
                           llm_api_key="sk-test",
                           llm_model="gpt-4o")
        assert resp.status_code == 200
        opts = m.call_args.kwargs["options"]
        assert opts.images == "caption"
        assert opts.vision_api_key  == "sk-test"
        assert opts.vision_provider == "openai"


# ── POST /api/convert — paginate_output ──────────────────────────────────────

class TestPaginateOutput:

    def test_paginate_output_true_forwarded(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx", paginate_output="true")
        assert m.call_args.kwargs["options"].paginate_output is True

    def test_paginate_output_default_false(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx")
        assert m.call_args.kwargs["options"].paginate_output is False


# ── POST /api/convert — speaker_labels ───────────────────────────────────────

class TestSpeakerLabels:
    """speaker_labels toggles audio diarization. On a non-audio file the field
    still flows into ParseOptions; the audio parser is the only consumer."""

    def test_speaker_labels_default_true(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx")
        assert m.call_args.kwargs["options"].speaker_labels is True

    def test_speaker_labels_false_forwarded(self, client):
        with patch("distill.convert", return_value=_mock_result()) as m:
            _upload(client, filename="doc.docx", speaker_labels="false")
        assert m.call_args.kwargs["options"].speaker_labels is False


# ── POST /api/convert — output_format = chunks ───────────────────────────────

class TestChunksOutput:
    """The chunks envelope must be downloadable JSON with a valid chunk shape."""

    def test_chunks_format_returns_200(self, client):
        with patch("distill.convert", return_value=_mock_chunks_result()):
            resp = _upload(client, filename="doc.docx", output_format="chunks")
        assert resp.status_code == 200

    def test_chunks_format_content_type_is_json(self, client):
        with patch("distill.convert", return_value=_mock_chunks_result()):
            resp = _upload(client, filename="doc.docx", output_format="chunks")
        assert "application/json" in resp.headers["content-type"]

    def test_chunks_envelope_shape(self, client):
        with patch("distill.convert", return_value=_mock_chunks_result()):
            resp = _upload(client, filename="doc.docx", output_format="chunks")
        body = resp.json()
        assert "chunks" in body
        assert "chunk_count" in body
        assert body["chunk_count"] == 2
        assert isinstance(body["chunks"], list)
        assert len(body["chunks"]) == 2

    def test_chunks_serialise_retains_per_chunk_fields(self, client):
        with patch("distill.convert", return_value=_mock_chunks_result()):
            resp = _upload(client, filename="doc.docx", output_format="chunks")
        first = resp.json()["chunks"][0]
        assert "chunk_id" in first
        assert "content"  in first
        assert first["chunk_id"] == "c1"


# ── POST /api/convert — extract = true without schema ───────────────────────

class TestExtractValidation:
    """extract=true demands a non-empty schema — validation runs before convert."""

    def test_extract_true_without_schema_returns_422(self, client):
        resp = _upload(client, filename="doc.docx", extract="true")
        assert resp.status_code == 422
        assert "non-empty schema" in resp.json()["detail"]

    def test_extract_true_empty_schema_returns_422(self, client):
        resp = _upload(client, filename="doc.docx", extract="true", schema="")
        assert resp.status_code == 422
        assert "non-empty schema" in resp.json()["detail"]

    def test_extract_true_whitespace_only_schema_returns_422(self, client):
        resp = _upload(client, filename="doc.docx", extract="true", schema="   \t\n")
        assert resp.status_code == 422
        assert "non-empty schema" in resp.json()["detail"]

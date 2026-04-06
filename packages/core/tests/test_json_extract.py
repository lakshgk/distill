"""
Tests for distill.features.json_extract — JSONExtractor, ExtractionError,
and the extract pipeline integration.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from distill.features.json_extract import ExtractionError, JSONExtractor
from distill.features.llm import LLMError


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_client(return_value=None, side_effect=None):
    c = MagicMock()
    if side_effect is not None:
        c.complete.side_effect = side_effect
    else:
        c.complete.return_value = return_value or "{}"
    return c


def _mock_convert_result(extracted=None):
    from distill.quality import QualityScore
    qs = QualityScore(
        overall=0.9, heading_preservation=1.0,
        table_preservation=1.0, list_preservation=1.0,
        token_reduction_ratio=0.8,
    )
    r = MagicMock()
    r.markdown = "# Hello\n\nWorld."
    r.quality_score = 0.9
    r.quality_details = qs
    r.warnings = []
    r.structured_warnings = []
    m = MagicMock()
    m.word_count = 10
    m.page_count = 1
    m.slide_count = None
    m.sheet_count = None
    m.source_format = "docx"
    r.metadata = m
    r.chunks = None
    r.document_json = None
    r.html = None
    r.extracted = extracted
    return r


# ── Test 1: valid JSON extraction ────────────────────────────────────────────

def test_extract_returns_correct_dict():
    data = {"name": "Acme", "year": 2024}
    client = _mock_client(return_value=json.dumps(data))
    result = JSONExtractor(client).extract("some md", {"name": "str", "year": "int"})
    assert result == data


# ── Test 2: strips markdown fences ───────────────────────────────────────────

def test_extract_strips_markdown_fences():
    data = {"title": "Report"}
    fenced = f"```json\n{json.dumps(data)}\n```"
    client = _mock_client(return_value=fenced)
    result = JSONExtractor(client).extract("md", {"title": "str"})
    assert result == data


# ── Test 3: retries once on invalid JSON, succeeds second attempt ────────────

def test_extract_retries_and_succeeds():
    data = {"field": "value"}
    client = _mock_client()
    client.complete.side_effect = ["not json", json.dumps(data)]
    result = JSONExtractor(client).extract("md", {"field": "str"})
    assert result == data
    assert client.complete.call_count == 2


# ── Test 4: raises ExtractionError after two failed parses ───────────────────

def test_extract_raises_after_two_failures():
    client = _mock_client()
    client.complete.return_value = "still not json"
    with pytest.raises(ExtractionError, match="Failed to parse JSON"):
        JSONExtractor(client).extract("md", {"f": "str"})


# ── Test 5: raises ValueError for empty schema ──────────────────────────────

def test_extract_raises_value_error_for_empty_schema():
    client = _mock_client()
    with pytest.raises(ValueError, match="non-empty dict"):
        JSONExtractor(client).extract("md", {})


# ── Test 6: wraps LLMError in ExtractionError ───────────────────────────────

def test_extract_wraps_llm_error():
    client = _mock_client(side_effect=LLMError("connection refused"))
    with pytest.raises(ExtractionError, match="LLM call failed"):
        JSONExtractor(client).extract("md", {"f": "str"})


# ── Test 7: convert() stores extracted in ConversionResult ───────────────────

def test_convert_stores_extracted():
    extracted = {"title": "My Doc"}
    mock_result = _mock_convert_result(extracted=extracted)
    assert mock_result.extracted == extracted


# ── Test 8: API 422 when extract=True but schema missing ────────────────────

def test_api_422_without_schema():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    client = TestClient(build_app())
    with patch("distill.convert"):
        files = {"file": ("doc.docx", io.BytesIO(b"fake"), "application/octet-stream")}
        data = {"extract": "true", "llm_api_key": "sk-test", "llm_model": "gpt-4o"}
        resp = client.post("/api/convert", data=data, files=files)
    assert resp.status_code == 422
    assert "schema" in resp.json()["detail"].lower()


# ── Test 9: API 422 when extract=True but llm_api_key empty ─────────────────

def test_api_422_without_llm_api_key():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    client = TestClient(build_app())
    with patch("distill.convert"):
        files = {"file": ("doc.docx", io.BytesIO(b"fake"), "application/octet-stream")}
        data = {"extract": "true", "schema": '{"title":"str"}'}
        resp = client.post("/api/convert", data=data, files=files)
    assert resp.status_code == 422
    assert "llm_api_key" in resp.json()["detail"]


# ── Test 10: API 422 when schema is not valid JSON ──────────────────────────

def test_api_422_invalid_json_schema():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    client = TestClient(build_app())
    with patch("distill.convert"):
        files = {"file": ("doc.docx", io.BytesIO(b"fake"), "application/octet-stream")}
        data = {"extract": "true", "schema": "not json", "llm_api_key": "k", "llm_model": "m"}
        resp = client.post("/api/convert", data=data, files=files)
    assert resp.status_code == 422
    assert "not valid JSON" in resp.json()["detail"]


# ── Test 11: API 422 when schema is valid JSON but not a dict ────────────────

def test_api_422_schema_not_dict():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    client = TestClient(build_app())
    with patch("distill.convert"):
        files = {"file": ("doc.docx", io.BytesIO(b"fake"), "application/octet-stream")}
        data = {"extract": "true", "schema": '["a","b"]', "llm_api_key": "k", "llm_model": "m"}
        resp = client.post("/api/convert", data=data, files=files)
    assert resp.status_code == 422
    assert "dict" in resp.json()["detail"].lower()


# ── Test 12: API response includes extracted key when extraction succeeds ────

def test_api_includes_extracted_key():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    mock_result = _mock_convert_result(extracted={"title": "Test"})

    client = TestClient(build_app())
    with patch("distill.convert", return_value=mock_result):
        files = {"file": ("doc.docx", io.BytesIO(b"fake"), "application/octet-stream")}
        data = {
            "extract": "true",
            "schema": '{"title":"str"}',
            "llm_api_key": "sk-test",
            "llm_model": "gpt-4o",
        }
        resp = client.post("/api/convert", data=data, files=files)

    assert resp.status_code == 200
    body = resp.json()
    assert "extracted" in body
    assert body["extracted"] == {"title": "Test"}


# ── Test 13: API response omits extracted key when extract=False ─────────────

def test_api_omits_extracted_when_not_requested():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    mock_result = _mock_convert_result()

    client = TestClient(build_app())
    with patch("distill.convert", return_value=mock_result):
        files = {"file": ("doc.docx", io.BytesIO(b"fake"), "application/octet-stream")}
        data = {}
        resp = client.post("/api/convert", data=data, files=files)

    assert resp.status_code == 200
    body = resp.json()
    assert "extracted" not in body

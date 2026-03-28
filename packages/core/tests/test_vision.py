"""
tests/test_vision.py
~~~~~~~~~~~~~~~~~~~~
Tests for the vision captioning pipeline (_vision.py).

All external API calls (OpenAI, Anthropic, Ollama) are mocked.
No network calls are made.
"""

from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from distill.ir import (
    Document,
    DocumentMetadata,
    Image,
    ImageType,
    Paragraph,
    Section,
    TextRun,
)
from distill.parsers._vision import (
    _collect_images,
    _image_to_b64,
    caption_images,
)
from distill.parsers.base import ParseOptions


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def png_file(tmp_path) -> Path:
    """Write a minimal PNG-like file and return its path."""
    p = tmp_path / "test.png"
    # Minimal PNG header bytes (not a valid PNG, but enough for base64 encoding tests)
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return p


@pytest.fixture()
def jpg_file(tmp_path) -> Path:
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
    return p


def _doc_with_image(path: str | None, image_type=ImageType.PHOTO) -> Document:
    return Document(
        metadata=DocumentMetadata(title="Test"),
        sections=[
            Section(
                level=1,
                heading=[TextRun("Section")],
                blocks=[Image(path=path, image_type=image_type)],
            )
        ],
    )


def _options(provider: str, **extra) -> ParseOptions:
    return ParseOptions(images="caption", vision_provider=provider, extra=extra)


# ── _collect_images ───────────────────────────────────────────────────────────

class TestCollectImages:

    def test_finds_image_in_section(self):
        doc = _doc_with_image("/some/path.png")
        imgs = _collect_images(doc)
        assert len(imgs) == 1

    def test_finds_image_in_subsection(self):
        sub = Section(level=2, blocks=[Image(path="/sub.png")])
        top = Section(level=1, blocks=[], subsections=[sub])
        doc = Document(sections=[top])
        imgs = _collect_images(doc)
        assert len(imgs) == 1
        assert imgs[0].path == "/sub.png"

    def test_finds_multiple_images(self):
        section = Section(level=1, blocks=[
            Image(path="/a.png"),
            Paragraph(runs=[TextRun("text")]),
            Image(path="/b.png"),
        ])
        doc = Document(sections=[section])
        assert len(_collect_images(doc)) == 2

    def test_empty_document_returns_empty(self):
        assert _collect_images(Document()) == []

    def test_no_images_returns_empty(self):
        doc = Document(sections=[
            Section(level=1, blocks=[Paragraph(runs=[TextRun("hello")])])
        ])
        assert _collect_images(doc) == []

    def test_deep_nesting(self):
        leaf = Section(level=3, blocks=[Image(path="/deep.png")])
        mid  = Section(level=2, subsections=[leaf])
        top  = Section(level=1, subsections=[mid])
        doc  = Document(sections=[top])
        imgs = _collect_images(doc)
        assert len(imgs) == 1
        assert imgs[0].path == "/deep.png"


# ── _image_to_b64 ─────────────────────────────────────────────────────────────

class TestImageToB64:

    def test_returns_base64_string(self, png_file):
        b64, _ = _image_to_b64(str(png_file))
        decoded = base64.b64decode(b64)
        assert decoded == png_file.read_bytes()

    def test_png_media_type(self, png_file):
        _, media_type = _image_to_b64(str(png_file))
        assert media_type == "image/png"

    def test_jpg_media_type(self, jpg_file):
        _, media_type = _image_to_b64(str(jpg_file))
        assert media_type == "image/jpeg"

    def test_unknown_extension_defaults_to_png(self, tmp_path):
        p = tmp_path / "file.bmp"
        p.write_bytes(b"BM" + b"\x00" * 10)
        _, media_type = _image_to_b64(str(p))
        assert media_type == "image/png"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            _image_to_b64("/nonexistent/path/image.png")


# ── caption_images — skip rules ───────────────────────────────────────────────

class TestSkipRules:

    def test_no_provider_is_noop(self, png_file):
        doc = _doc_with_image(str(png_file))
        caption_images(doc, ParseOptions())
        assert not doc.warnings
        assert _collect_images(doc)[0].caption is None

    def test_images_not_caption_mode_is_noop(self, png_file):
        doc = _doc_with_image(str(png_file))
        caption_images(doc, ParseOptions(images="extract", vision_provider="openai"))
        assert _collect_images(doc)[0].caption is None

    def test_path_none_skipped_silently(self):
        doc = _doc_with_image(None)
        caption_images(doc, _options("openai"))
        assert not doc.warnings

    def test_decorative_image_skipped_silently(self, png_file):
        doc = _doc_with_image(str(png_file), image_type=ImageType.DECORATIVE)
        caption_images(doc, _options("openai"))
        assert not doc.warnings
        assert _collect_images(doc)[0].caption is None

    def test_already_captioned_not_recaptioned(self, png_file):
        doc = _doc_with_image(str(png_file))
        _collect_images(doc)[0].caption = "pre-existing"
        caption_images(doc, _options("openai"))
        assert _collect_images(doc)[0].caption == "pre-existing"

    def test_empty_document_is_noop(self):
        doc = Document()
        caption_images(doc, _options("openai"))
        assert not doc.warnings

    def test_unknown_provider_appends_warning(self, png_file):
        doc = _doc_with_image(str(png_file))
        caption_images(doc, _options("unknown_llm"))
        assert len(doc.warnings) == 1
        assert "unknown" in doc.warnings[0]

    def test_missing_vision_package_appends_warning(self, png_file):
        doc = _doc_with_image(str(png_file))
        saved = sys.modules.get("openai")
        sys.modules["openai"] = None
        try:
            caption_images(doc, _options("openai"))
            assert any("skipped" in w or "install" in w.lower() for w in doc.warnings)
        finally:
            if saved is None:
                sys.modules.pop("openai", None)
            else:
                sys.modules["openai"] = saved


# ── OpenAI provider ───────────────────────────────────────────────────────────

class TestOpenAIProvider:

    def _mock_openai(self, caption: str):
        choice   = MagicMock()
        choice.message.content = caption
        response = MagicMock()
        response.choices = [choice]
        client   = MagicMock()
        client.chat.completions.create.return_value = response
        openai_mod = MagicMock()
        openai_mod.OpenAI.return_value = client
        return openai_mod, client

    def test_caption_written_to_image(self, png_file):
        openai_mod, _ = self._mock_openai("A bar chart showing Q4 revenue.")
        with patch.dict(sys.modules, {"openai": openai_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("openai"))
        assert _collect_images(doc)[0].caption == "A bar chart showing Q4 revenue."

    def test_uses_api_key_from_options(self, png_file):
        openai_mod, _ = self._mock_openai("caption")
        with patch.dict(sys.modules, {"openai": openai_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("openai", vision_api_key="sk-test"))  # noqa
        openai_mod.OpenAI.assert_called_once()
        call_kwargs = openai_mod.OpenAI.call_args
        assert call_kwargs is not None

    def test_uses_default_model(self, png_file):
        openai_mod, client = self._mock_openai("caption")
        with patch.dict(sys.modules, {"openai": openai_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("openai"))
        call_kwargs = client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4o"

    def test_custom_model_override(self, png_file):
        openai_mod, client = self._mock_openai("caption")
        with patch.dict(sys.modules, {"openai": openai_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("openai", vision_model="gpt-4-turbo"))
        call_kwargs = client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4-turbo"

    def test_api_error_adds_warning_not_raises(self, png_file):
        openai_mod = MagicMock()
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("quota exceeded")
        openai_mod.OpenAI.return_value = client
        with patch.dict(sys.modules, {"openai": openai_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("openai"))
        assert len(doc.warnings) == 1
        assert "quota exceeded" in doc.warnings[0]

    def test_multiple_images_all_captioned(self, tmp_path):
        img1 = tmp_path / "a.png"
        img2 = tmp_path / "b.png"
        img1.write_bytes(b"x" * 8)
        img2.write_bytes(b"x" * 8)
        captions = ["Caption for A.", "Caption for B."]
        idx = [0]
        def _side_effect(**kwargs):
            r = MagicMock()
            r.choices[0].message.content = captions[idx[0]]
            idx[0] += 1
            return r
        openai_mod = MagicMock()
        client = MagicMock()
        client.chat.completions.create.side_effect = _side_effect
        openai_mod.OpenAI.return_value = client
        section = Section(level=1, blocks=[
            Image(path=str(img1), image_type=ImageType.CHART),
            Image(path=str(img2), image_type=ImageType.DIAGRAM),
        ])
        doc = Document(sections=[section])
        with patch.dict(sys.modules, {"openai": openai_mod}):
            caption_images(doc, _options("openai"))
        imgs = _collect_images(doc)
        assert imgs[0].caption == "Caption for A."
        assert imgs[1].caption == "Caption for B."


# ── Anthropic provider ────────────────────────────────────────────────────────

class TestAnthropicProvider:

    def _mock_anthropic(self, caption: str):
        content_block = MagicMock()
        content_block.text = caption
        response = MagicMock()
        response.content = [content_block]
        client = MagicMock()
        client.messages.create.return_value = response
        anthropic_mod = MagicMock()
        anthropic_mod.Anthropic.return_value = client
        return anthropic_mod, client

    def test_caption_written_to_image(self, png_file):
        anthropic_mod, _ = self._mock_anthropic("A flowchart with four steps.")
        with patch.dict(sys.modules, {"anthropic": anthropic_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("anthropic"))
        assert _collect_images(doc)[0].caption == "A flowchart with four steps."

    def test_uses_default_model(self, png_file):
        anthropic_mod, client = self._mock_anthropic("caption")
        with patch.dict(sys.modules, {"anthropic": anthropic_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("anthropic"))
        call_kwargs = client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-3-5-haiku-latest"

    def test_custom_model_override(self, png_file):
        anthropic_mod, client = self._mock_anthropic("caption")
        with patch.dict(sys.modules, {"anthropic": anthropic_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("anthropic", vision_model="claude-3-opus-20240229"))
        call_kwargs = client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-3-opus-20240229"

    def test_api_error_adds_warning_not_raises(self, png_file):
        anthropic_mod = MagicMock()
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("rate limit")
        anthropic_mod.Anthropic.return_value = client
        with patch.dict(sys.modules, {"anthropic": anthropic_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("anthropic"))
        assert len(doc.warnings) == 1
        assert "rate limit" in doc.warnings[0]


# ── Ollama provider ───────────────────────────────────────────────────────────

class TestOllamaProvider:

    def _mock_ollama(self, caption: str):
        response = {"message": {"content": caption}}
        client = MagicMock()
        client.chat.return_value = response
        ollama_mod = MagicMock()
        ollama_mod.Client.return_value = client
        return ollama_mod, client

    def test_caption_written_to_image(self, png_file):
        ollama_mod, _ = self._mock_ollama("A pie chart showing market share.")
        with patch.dict(sys.modules, {"ollama": ollama_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("ollama"))
        assert _collect_images(doc)[0].caption == "A pie chart showing market share."

    def test_uses_default_model(self, png_file):
        ollama_mod, client = self._mock_ollama("caption")
        with patch.dict(sys.modules, {"ollama": ollama_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("ollama"))
        call_kwargs = client.chat.call_args[1]
        assert call_kwargs["model"] == "llava"

    def test_custom_base_url(self, png_file):
        ollama_mod, _ = self._mock_ollama("caption")
        with patch.dict(sys.modules, {"ollama": ollama_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("ollama", vision_base_url="http://myserver:11434"))
        ollama_mod.Client.assert_called_once_with(host="http://myserver:11434")

    def test_default_base_url(self, png_file):
        ollama_mod, _ = self._mock_ollama("caption")
        with patch.dict(sys.modules, {"ollama": ollama_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("ollama"))
        ollama_mod.Client.assert_called_once_with(host="http://localhost:11434")

    def test_api_error_adds_warning_not_raises(self, png_file):
        ollama_mod = MagicMock()
        client = MagicMock()
        client.chat.side_effect = RuntimeError("model not found")
        ollama_mod.Client.return_value = client
        with patch.dict(sys.modules, {"ollama": ollama_mod}):
            doc = _doc_with_image(str(png_file))
            caption_images(doc, _options("ollama"))
        assert len(doc.warnings) == 1
        assert "model not found" in doc.warnings[0]


# ── Integration: caption appears in rendered Markdown ────────────────────────

class TestRenderIntegration:

    def test_caption_rendered_over_alt_text(self, png_file):
        img = Image(
            path=str(png_file),
            alt_text="figure_3.png",
            image_type=ImageType.CHART,
        )
        section = Section(level=1, heading=[TextRun("Results")], blocks=[img])
        doc = Document(sections=[section])

        openai_mod = MagicMock()
        choice = MagicMock()
        choice.message.content = "Revenue increased 40% year over year."
        response = MagicMock()
        response.choices = [choice]
        client = MagicMock()
        client.chat.completions.create.return_value = response
        openai_mod.OpenAI.return_value = client

        with patch.dict(sys.modules, {"openai": openai_mod}):
            caption_images(doc, _options("openai"))

        markdown = doc.render()
        assert "Revenue increased 40% year over year." in markdown
        # alt_text should not appear since caption takes priority
        assert "figure_3.png" not in markdown

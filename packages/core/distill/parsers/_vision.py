"""
distill.parsers._vision
~~~~~~~~~~~~~~~~~~~~~~~
Vision captioning pipeline for image nodes.

Replaces Image.caption with an LLM-generated description when the caller has
installed distill-core[vision] and configured a vision provider.

Public interface
----------------
    caption_images(doc, options) -> None   ← call this from convert()

Provider support
----------------
    openai     — gpt-4o (default); override via options.extra['vision_model']
    anthropic  — claude-3-5-haiku-latest (default); override via options.extra['vision_model']
    ollama     — llava (default); override via options.extra['vision_model']
                 Base URL configurable via options.extra['vision_base_url']
                 (default: http://localhost:11434)

Graceful degradation
--------------------
If distill-core[vision] is not installed, caption_images() appends one warning
to doc.warnings and returns without raising. No error, no surprise API calls.

Images are skipped silently when:
    - image.path is None  (no file was extracted to disk)
    - image.image_type == ImageType.DECORATIVE
    - image.caption is already populated
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from distill.ir import Document, Image, ImageType, Section
from distill.parsers.base import ParseOptions


# ── Default models per provider ───────────────────────────────────────────────

_DEFAULT_MODEL: dict[str, str] = {
    "openai":    "gpt-4o",
    "anthropic": "claude-3-5-haiku-latest",
    "ollama":    "llava",
}

_CAPTION_PROMPT = (
    "Describe this image in one or two concise sentences suitable for use as "
    "alt text in a technical document. Focus on the content and meaning, not "
    "visual style. Do not start with 'This image shows' or 'The image depicts'."
)


# ── Entry point ───────────────────────────────────────────────────────────────

def caption_images(doc: Document, options: ParseOptions) -> None:
    """
    Walk all Image nodes in *doc* and populate Image.caption using the
    configured vision provider.

    Modifies *doc* in place. Safe to call when distill-core[vision] is not
    installed — appends one warning to doc.warnings and returns silently.
    """
    if options.images != "caption":
        return

    provider = (options.vision_provider or "").lower().strip()
    if not provider:
        return

    images = _collect_images(doc)
    if not images:
        return

    to_caption = [
        img for img in images
        if img.path is not None
        and img.image_type != ImageType.DECORATIVE
        and not img.caption
    ]
    if not to_caption:
        return

    model = options.extra.get("vision_model") or _DEFAULT_MODEL.get(provider)

    try:
        if provider == "openai":
            _caption_via_openai(to_caption, model, options, doc)
        elif provider == "anthropic":
            _caption_via_anthropic(to_caption, model, options, doc)
        elif provider == "ollama":
            _caption_via_ollama(to_caption, model, options, doc)
        else:
            doc.warnings.append(
                f"Vision captioning: unknown provider {provider!r}. "
                "Supported: 'openai', 'anthropic', 'ollama'."
            )
    except ImportError as exc:
        doc.warnings.append(
            f"Vision captioning skipped — {exc}. "
            "Install with: pip install distill-core[vision]"
        )


# ── Image collection ──────────────────────────────────────────────────────────

def _collect_images(doc: Document) -> list[Image]:
    images: list[Image] = []
    for section in doc.sections:
        _collect_from_section(section, images)
    return images


def _collect_from_section(section: Section, out: list[Image]) -> None:
    for block in section.blocks:
        if isinstance(block, Image):
            out.append(block)
    for sub in section.subsections:
        _collect_from_section(sub, out)


# ── Image encoding ────────────────────────────────────────────────────────────

def _image_to_b64(path: str) -> tuple[str, str]:
    """
    Return (base64_data, media_type) for the image at *path*.
    Raises FileNotFoundError if the file does not exist.
    """
    suffix = Path(path).suffix.lower()
    media_type = {
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png":  "image/png",
        ".gif":  "image/gif",
        ".webp": "image/webp",
    }.get(suffix, "image/png")
    data = base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")
    return data, media_type


# ── OpenAI provider ───────────────────────────────────────────────────────────

def _caption_via_openai(
    images: list[Image],
    model: Optional[str],
    options: ParseOptions,
    doc: Document,
) -> None:
    import openai  # noqa: PLC0415

    api_key = options.vision_api_key or options.extra.get("openai_api_key")
    client  = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()

    for img in images:
        try:
            b64, media_type = _image_to_b64(img.path)
            response = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _CAPTION_PROMPT},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{media_type};base64,{b64}",
                            "detail": "low",
                        }},
                    ],
                }],
                max_tokens=150,
            )
            img.caption = response.choices[0].message.content.strip()
        except Exception as exc:
            doc.warnings.append(f"Vision captioning failed for {img.path!r}: {exc}")


# ── Anthropic provider ────────────────────────────────────────────────────────

def _caption_via_anthropic(
    images: list[Image],
    model: Optional[str],
    options: ParseOptions,
    doc: Document,
) -> None:
    import anthropic as anthropic_sdk  # noqa: PLC0415

    api_key = options.vision_api_key or options.extra.get("anthropic_api_key")
    client  = (
        anthropic_sdk.Anthropic(api_key=api_key)
        if api_key
        else anthropic_sdk.Anthropic()
    )

    for img in images:
        try:
            b64, media_type = _image_to_b64(img.path)
            response = client.messages.create(
                model=model,
                max_tokens=150,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        }},
                        {"type": "text", "text": _CAPTION_PROMPT},
                    ],
                }],
            )
            img.caption = response.content[0].text.strip()
        except Exception as exc:
            doc.warnings.append(f"Vision captioning failed for {img.path!r}: {exc}")


# ── Ollama provider ───────────────────────────────────────────────────────────

def _caption_via_ollama(
    images: list[Image],
    model: Optional[str],
    options: ParseOptions,
    doc: Document,
) -> None:
    import ollama  # noqa: PLC0415

    base_url = options.extra.get("vision_base_url", "http://localhost:11434")
    client   = ollama.Client(host=base_url)

    for img in images:
        try:
            b64, _ = _image_to_b64(img.path)
            response = client.chat(
                model=model,
                messages=[{
                    "role": "user",
                    "content": _CAPTION_PROMPT,
                    "images": [b64],
                }],
            )
            img.caption = response["message"]["content"].strip()
        except Exception as exc:
            doc.warnings.append(f"Vision captioning failed for {img.path!r}: {exc}")

"""
distill.parsers.base
~~~~~~~~~~~~~~~~~~~~
Base class and protocol for all format parsers.
Every parser must implement the Parser interface.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from distill.warnings import WarningCollector


@dataclass
class ParseOptions:
    """Options passed to every parser at conversion time."""
    # Image handling
    image_dir:        Optional[str]   = None     # directory to write extracted images
    images:           str             = "extract" # extract | suppress | inline_ocr | caption
    vision_provider:  Optional[str]   = None      # None | "openai" | "anthropic" | "ollama"
    vision_api_key:   Optional[str]   = None
    vision_base_url:  Optional[str]   = None      # OpenAI-compatible base URL override

    # Table handling
    max_table_rows:   int             = 500       # cap rows per table; 0 = unlimited
    include_formulas: bool            = False     # XLSX: render formula text vs computed value

    # Streaming
    streaming:        bool            = False

    # Quality
    min_quality:      float           = 0.0       # 0.0 = no minimum enforced

    # OCR — whether OCR is available/enabled for scanned PDFs
    ocr_enabled:      bool            = True

    # Extra per-format options passed through
    extra:            dict            = field(default_factory=dict)

    # Output format — controls what convert() returns
    # Accepted values: "markdown" | "json" | "html" | "chunks"
    output_format:    str                        = "markdown"

    # Pagination — insert page separators at page boundaries (PDF/DOCX only)
    paginate_output:  bool                       = False

    # HTML input: strip boilerplate via trafilatura / readability-lxml
    extract_content:  bool                       = False

    # LLM configuration — set by caller for LLM-powered features
    llm:              Optional["LLMConfig"] = field(default=None, repr=False)

    # LLM-powered cross-page table merging (PDF only)
    llm_merge_tables: bool            = False

    # Structured JSON extraction via LLM
    extract:          bool            = False
    schema:           Optional[dict]  = field(default=None, repr=False)

    # Audio topic segmentation (requires LLM)
    topic_segmentation: bool             = False

    # Audio pipeline
    transcription_engine: str              = "whisper"
    whisper_model:        str              = "base"
    hf_token:             Optional[str]    = None
    speaker_labels:       bool             = True      # run diarization to tag speaker turns

    # Warning collector — set by convert() before parse(); parsers may call collector.add()
    collector:        Optional[WarningCollector] = field(default=None, repr=False)


class Parser(abc.ABC):
    """
    Abstract base class for all Distill format parsers.

    Subclasses register themselves via the @registry.register decorator
    and implement parse() to return an IR Document.
    """

    #: MIME types this parser handles, in order of preference
    mime_types: list[str] = []

    #: File extensions this parser handles (lowercase, with dot)
    extensions: list[str] = []

    #: Python packages required for this parser (checked at registration time)
    requires: list[str] = []

    #: Optional packages that unlock additional features if present
    optional_requires: list[str] = []

    #: Whether this parser requires LibreOffice for legacy format conversion
    requires_libreoffice: bool = False

    @abc.abstractmethod
    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> "Document":  # noqa: F821
        """
        Parse the source document and return an IR Document.

        Args:
            source: file path, Path object, or raw bytes
            options: ParseOptions controlling image handling, table limits, etc.

        Returns:
            A populated IR Document tree.

        Raises:
            UnsupportedFormatError: if the source cannot be handled by this parser
            ParseError: if parsing fails due to a malformed or unsupported document
        """
        ...

    @classmethod
    def is_available(cls) -> bool:
        """Return True if all required packages are importable."""
        import importlib
        for pkg in cls.requires:
            try:
                importlib.import_module(pkg.replace("-", "_"))
            except ImportError:
                return False
        return True

    @classmethod
    def missing_requires(cls) -> list[str]:
        """Return the list of required packages that are not installed."""
        import importlib
        missing = []
        for pkg in cls.requires:
            try:
                importlib.import_module(pkg.replace("-", "_"))
            except ImportError:
                missing.append(pkg)
        return missing


# ── Exceptions ───────────────────────────────────────────────────────────────

class DistillError(Exception):
    """Base exception for all Distill errors."""


class UnsupportedFormatError(DistillError):
    """Raised when no parser is available for the given format."""
    def __init__(self, fmt: str, install_hint: Optional[str] = None):
        self.fmt = fmt
        self.install_hint = install_hint
        msg = f"No parser available for format: {fmt!r}"
        if install_hint:
            msg += f". Install hint: {install_hint}"
        super().__init__(msg)


class ParseError(DistillError):
    """Raised when a parser fails to process a document."""


AUDIO_IMPORT_ERROR = (
    "Audio support requires additional dependencies. "
    "Install them with: pip install distill-core[audio]"
)


# ── Image classification helper ─────────────────────────────────────────────

_PPTX_DECORATIVE_PATTERNS = [
    "background", "bg_", "rule_", "accent_", "divider",
    "line_", "border_", "fill_", "stripe_",
]

_DOCX_DECORATIVE_PATTERNS = [
    "background", "bg_", "rule_", "accent_", "divider",
    "line_", "border_", "fill_", "stripe_", "watermark",
    "picture 0", "picture 1", "picture 2", "picture 3",
]


def classify_image(
    *,
    mode: str,
    # PPTX fields
    shape_w: int = 0,
    shape_h: int = 0,
    slide_w: int = 0,
    slide_h: int = 0,
    name: str = "",
    # PDF fields
    img_w: float = 0.0,
    img_h: float = 0.0,
    page_w: float = 0.0,
    page_h: float = 0.0,
    # DOCX fields
    alt: str = "",
) -> "ImageType":
    """Classify an image as decorative or content based on format-specific rules."""
    from distill.ir import ImageType

    if mode == "pptx":
        # Rule 1 — Full-bleed background (shape covers ~100% of slide, not oversize)
        if slide_w > 0 and slide_h > 0:
            w_ratio = shape_w / slide_w
            h_ratio = shape_h / slide_h
            if 0.85 <= w_ratio <= 1.05 and 0.85 <= h_ratio <= 1.05:
                return ImageType.DECORATIVE

        # Rule 2 — Thin rule line (extreme aspect ratio)
        if shape_h > 0:
            aspect = shape_w / shape_h
            if aspect > 15 or aspect < 0.067:
                return ImageType.DECORATIVE

        # Rule 3 — Tiny accent (less than 5% of slide in both dimensions)
        if slide_w > 0 and slide_h > 0:
            if shape_w < slide_w * 0.05 and shape_h < slide_h * 0.05:
                return ImageType.DECORATIVE

        # Rule 4 — Name pattern match
        if any(name.lower().startswith(p) for p in _PPTX_DECORATIVE_PATTERNS):
            return ImageType.DECORATIVE

        return ImageType.UNKNOWN

    elif mode == "pdf":
        # Rule 1 — Full-bleed background
        if page_w > 0 and page_h > 0:
            if img_w >= page_w * 0.85 and img_h >= page_h * 0.85:
                return ImageType.DECORATIVE

        # Rule 2 — Thin rule line
        if img_h > 0:
            aspect = img_w / img_h
            if aspect > 15 or aspect < 0.067:
                return ImageType.DECORATIVE

        # Rule 3 — Tiny accent
        if page_w > 0 and page_h > 0:
            if img_w < page_w * 0.05 and img_h < page_h * 0.05:
                return ImageType.DECORATIVE

        return ImageType.UNKNOWN

    elif mode == "docx":
        if any(alt.lower().startswith(p) for p in _DOCX_DECORATIVE_PATTERNS):
            return ImageType.DECORATIVE
        return ImageType.UNKNOWN

    return ImageType.UNKNOWN


# ── Image extraction helper ─────────────────────────────────────────────────

def extract_image(
    image_bytes: bytes,
    ext: str,
    image_dir: Path,
    filename: str,
    collector: "WarningCollector | None" = None,
) -> str | None:
    """Write image bytes to disk and return the path, or None on failure."""
    from distill.warnings import ConversionWarning, WarningType

    fname = f"{filename}.{ext.lstrip('.')}"
    target = image_dir / fname
    try:
        image_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image_bytes)
        return str(target)
    except OSError as e:
        if collector is not None:
            collector.add(ConversionWarning(
                type=WarningType.image_write_failed,
                message=f"Failed to write image {filename}: {e}",
            ))
        return None

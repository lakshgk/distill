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
from typing import Optional, Union


@dataclass
class ParseOptions:
    """Options passed to every parser at conversion time."""
    # Image handling
    image_dir:        Optional[str]   = None     # directory to write extracted images
    images:           str             = "extract" # extract | suppress | inline_ocr | caption
    vision_provider:  Optional[str]   = None      # None | "openai" | "anthropic" | "ollama"
    vision_api_key:   Optional[str]   = None

    # Table handling
    max_table_rows:   int             = 500       # cap rows per table; 0 = unlimited
    include_formulas: bool            = False     # XLSX: render formula text vs computed value

    # Streaming
    streaming:        bool            = False

    # Quality
    min_quality:      float           = 0.0       # 0.0 = no minimum enforced

    # Extra per-format options passed through
    extra:            dict            = field(default_factory=dict)


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

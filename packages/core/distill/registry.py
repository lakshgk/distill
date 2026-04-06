"""
distill.registry
~~~~~~~~~~~~~~~~
Parser registry: maps file extensions and MIME types to Parser classes.
Parsers self-register via the @registry.register decorator.
Only parsers whose required dependencies are available are registered.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional, Type, Union

from distill.parsers.base import Parser, UnsupportedFormatError


class ConverterRegistry:
    """
    Central registry of available format parsers.

    Parsers are registered with their supported extensions and MIME types.
    At registration time, the registry checks that required packages are
    installed — parsers with missing deps are skipped with a warning.
    """

    def __init__(self):
        self._by_extension: dict[str, Type[Parser]] = {}
        self._by_mime:      dict[str, Type[Parser]] = {}
        self._all:          list[Type[Parser]]       = []
        self._skipped:      list[dict]               = []  # parsers skipped due to missing deps

    def register(self, parser_cls: Type[Parser]) -> Type[Parser]:
        """
        Decorator: register a Parser class.

        Usage:
            @registry.register
            class DocxParser(Parser):
                extensions = [".docx"]
                mime_types = ["application/vnd.openxmlformats-officedocument..."]
                requires   = ["mammoth"]
        """
        missing = parser_cls.missing_requires()
        if missing:
            self._skipped.append({
                "parser": parser_cls.__name__,
                "missing": missing,
                "hint": f"pip install distill-core[{parser_cls.__name__.lower().replace('parser','')}]"
            })
            return parser_cls  # still return the class, just don't register it

        for ext in parser_cls.extensions:
            self._by_extension[ext.lower()] = parser_cls

        for mime in parser_cls.mime_types:
            self._by_mime[mime] = parser_cls

        self._all.append(parser_cls)
        return parser_cls

    def find(self, source: Union[str, Path, bytes]) -> Type[Parser]:
        """
        Find the best available parser for the given source.

        Resolution order:
          1. File extension lookup
          2. MIME type sniffing
          3. UnsupportedFormatError with install hint if nothing matches
        """
        if isinstance(source, bytes):
            raise UnsupportedFormatError("bytes", "Pass a file path for format detection")

        path = Path(source)
        ext  = path.suffix.lower()

        # 1. Extension lookup
        if ext in self._by_extension:
            return self._by_extension[ext]

        # 2. MIME type sniffing
        mime, _ = mimetypes.guess_type(str(path))
        if mime and mime in self._by_mime:
            return self._by_mime[mime]

        # 3. Check if a skipped parser would have handled this
        hint = None
        for skipped in self._skipped:
            cls_name = skipped["parser"].lower()
            if ext.lstrip(".") in cls_name:
                hint = skipped["hint"]
                break

        raise UnsupportedFormatError(ext or str(path), hint)

    def supported_formats(self) -> list[dict]:
        """Return a list of supported format descriptors."""
        return [
            {
                "parser":     cls.__name__,
                "extensions": cls.extensions,
                "mime_types": cls.mime_types,
                "requires":   cls.requires,
            }
            for cls in self._all
        ]

    def available_parsers(self) -> list[Type[Parser]]:
        """Return all registered (available) parser classes."""
        return list(self._all)

    def skipped_parsers(self) -> list[dict]:
        """Return info about parsers skipped due to missing dependencies."""
        return list(self._skipped)


# Global singleton registry — import and use this everywhere
registry = ConverterRegistry()

# Register all built-in parsers (import triggers @registry.register decorators)
def _load_builtin_parsers():
    from distill.parsers import docx, xlsx, pptx, pdf, google, epub, json_parser  # noqa: F401

_load_builtin_parsers()

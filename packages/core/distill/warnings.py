"""
distill.warnings
~~~~~~~~~~~~~~~~
Structured warning system for Distill conversions.

Parsers and pipeline steps attach ConversionWarning objects to a
WarningCollector during processing. The collector is serialised to
JSON-safe dicts for API responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class WarningType(str, Enum):
    CROSS_PAGE_TABLE        = "cross_page_table"
    MATH_DETECTED           = "math_detected"
    MATH_CONVERSION_PARTIAL = "math_conversion_partial"
    SCANNED_CONTENT         = "scanned_content"
    AUDIO_QUALITY_LOW       = "audio_quality_low"
    AUDIO_MODEL_MISSING     = "audio_model_missing"
    TABLE_TRUNCATED         = "table_truncated"
    CONTENT_EXTRACTED       = "content_extracted"


@dataclass
class ConversionWarning:
    """A single structured warning produced during conversion."""
    type:    WarningType
    message: str
    pages:   Optional[list[int]] = field(default=None)
    count:   Optional[int]       = field(default=None)


class WarningCollector:
    """Accumulates ConversionWarning objects during a conversion pass."""

    def __init__(self) -> None:
        self._warnings: list[ConversionWarning] = []

    def add(self, warning: ConversionWarning) -> None:
        """Append a warning to the collection."""
        self._warnings.append(warning)

    def all(self) -> list[ConversionWarning]:
        """Return all collected warnings."""
        return list(self._warnings)

    def has(self, type: WarningType) -> bool:
        """Return True if at least one warning of the given type exists."""
        return any(w.type == type for w in self._warnings)

    def to_dict(self) -> list[dict]:
        """
        Serialise all warnings to a list of JSON-safe dicts.
        Optional fields (pages, count) are omitted when None.
        """
        result = []
        for w in self._warnings:
            entry: dict = {
                "type":    w.type.value,
                "message": w.message,
            }
            if w.pages is not None:
                entry["pages"] = w.pages
            if w.count is not None:
                entry["count"] = w.count
            result.append(entry)
        return result

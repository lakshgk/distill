"""
distill.features.json_extract
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Structured JSON extraction from document Markdown using an LLM.

The caller provides a schema dict describing the fields to extract.
The LLM is prompted to return valid JSON matching the schema.
"""

from __future__ import annotations

import json
import logging
import re

_logger = logging.getLogger(__name__)


# ── Exceptions ──────────────────────────────────────────────────────────────

class ExtractionError(Exception):
    """Raised when structured extraction fails after retries."""


# ── Extractor ───────────────────────────────────────────────────────────────

class JSONExtractor:
    """Extract structured data from Markdown using an LLM."""

    def __init__(self, client) -> None:
        self._client = client

    def extract(self, markdown: str, schema: dict) -> dict:
        """Extract values matching *schema* from the document *markdown*.

        Parameters
        ----------
        markdown:
            The rendered Markdown text of the document.
        schema:
            A dict mapping field names to expected type descriptions
            (e.g. ``{"parties": "list[str]", "date": "str"}``).

        Returns
        -------
        dict
            Parsed JSON matching the schema.

        Raises
        ------
        ValueError
            If *schema* is empty or not a dict.
        ExtractionError
            If the LLM fails to return valid JSON after retries.
        """
        if not isinstance(schema, dict) or not schema:
            raise ValueError("schema must be a non-empty dict")

        system = self._build_system_prompt(schema)

        # First attempt
        try:
            raw = self._client.complete(system, markdown)
        except Exception as exc:
            raise ExtractionError(f"LLM call failed: {exc}") from exc

        result = self._try_parse(raw)
        if result is not None:
            return result

        # Retry with correction prompt
        correction_system = (
            f"{system}\n\n"
            f"Your previous response was not valid JSON:\n{raw}\n\n"
            f"Please return ONLY valid JSON matching the schema. "
            f"No markdown fences, no prose."
        )
        try:
            raw2 = self._client.complete(correction_system, markdown)
        except Exception as exc:
            raise ExtractionError(
                f"LLM retry failed: {exc}. Original response: {raw[:200]}"
            ) from exc

        result2 = self._try_parse(raw2)
        if result2 is not None:
            return result2

        raise ExtractionError(
            f"Failed to parse JSON after 2 attempts. Last response: {raw2[:200]}"
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_system_prompt(schema: dict) -> str:
        fields = "\n".join(f"  - {name}: {typ}" for name, typ in schema.items())
        return (
            "You are a document data extraction assistant. "
            "Extract the following fields from the document and return "
            "ONLY valid JSON matching this schema — no prose, no markdown "
            "fences, no explanation:\n\n"
            f"{fields}\n\n"
            "If a field cannot be found, use null for its value."
        )

    @staticmethod
    def _try_parse(raw: str) -> dict | None:
        """Attempt to parse *raw* as JSON, stripping markdown fences if present."""
        text = raw.strip()

        # Strip ```json ... ``` or ``` ... ``` wrappers
        fence_match = re.match(
            r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", text, re.DOTALL
        )
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            return None
        except (json.JSONDecodeError, ValueError):
            return None

"""
distill.features.topic_segment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
LLM-powered topic segmentation for audio transcripts.

Groups consecutive speaker-turn paragraphs into named topic sections via
targeted LLM calls. Batches in groups of up to 20 paragraphs per call.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from distill.ir import Document, Paragraph, Section, TextRun

_logger = logging.getLogger(__name__)

_BATCH_SIZE = 20

_SYSTEM_PROMPT = (
    "You are a transcript segmentation assistant. Given a numbered list of "
    "transcript paragraphs, identify topic boundaries and assign a short "
    "descriptive topic name to each segment. Return ONLY a JSON array with "
    "no prose, no markdown fences. Each element must have exactly these "
    "fields: start_index (int), end_index (int, inclusive), topic (str). "
    "Indices are relative to the batch provided. Every paragraph index must "
    "be covered by exactly one segment. Topic names must be 2-6 words."
)


class TopicSegmenter:
    """Group transcript paragraphs into named topic sections via LLM."""

    def __init__(self, client) -> None:
        self._client = client

    def segment(self, doc: Document) -> Document:
        """Segment the document into topic sections. Never raises."""
        try:
            return self._segment_impl(doc)
        except Exception as exc:
            _logger.debug("TopicSegmenter.segment error: %s", exc)
            return doc

    def _segment_impl(self, doc: Document) -> Document:
        from distill.features.llm import LLMError

        # Collect all paragraphs with position metadata
        paras: list[tuple[Paragraph, int, int]] = []  # (para, section_idx, block_idx)
        for si, section in enumerate(doc.sections):
            for bi, block in enumerate(section.blocks):
                if isinstance(block, Paragraph):
                    paras.append((block, si, bi))

        if len(paras) < 3:
            return doc

        # Preserve level-1 heading sections
        heading_sections = [s for s in doc.sections if s.level == 1 and s.heading]

        # Process in batches
        all_segments: list[dict] = []  # {"start": global_idx, "end": global_idx, "topic": str}
        failed_indices: set[int] = set()

        for batch_start in range(0, len(paras), _BATCH_SIZE):
            batch = paras[batch_start:batch_start + _BATCH_SIZE]
            numbered = "\n".join(
                f"{i}. {self._para_text(p)}"
                for i, (p, _, _) in enumerate(batch)
            )

            try:
                raw = self._client.complete(_SYSTEM_PROMPT, numbered)
            except LLMError as exc:
                _logger.debug("TopicSegmenter LLM call failed: %s", exc)
                for i in range(len(batch)):
                    failed_indices.add(batch_start + i)
                continue
            except Exception as exc:
                _logger.debug("TopicSegmenter unexpected error: %s", exc)
                for i in range(len(batch)):
                    failed_indices.add(batch_start + i)
                continue

            parsed = self._parse_response(raw, len(batch))
            if parsed is None:
                for i in range(len(batch)):
                    failed_indices.add(batch_start + i)
                continue

            for seg in parsed:
                all_segments.append({
                    "start": batch_start + seg["start_index"],
                    "end": batch_start + seg["end_index"],
                    "topic": seg["topic"],
                })

        if not all_segments and not failed_indices:
            return doc

        # Rebuild sections
        new_sections: list[Section] = list(heading_sections)

        # Topic sections
        for seg in all_segments:
            topic = seg["topic"]
            section = Section(
                level=2,
                heading=[TextRun(text=topic)],
                blocks=[],
            )
            for idx in range(seg["start"], seg["end"] + 1):
                if 0 <= idx < len(paras):
                    section.blocks.append(paras[idx][0])
            if section.blocks:
                new_sections.append(section)

        # Uncategorised section for failed batches
        if failed_indices:
            uncategorised = Section(
                level=2,
                heading=[TextRun(text="Uncategorised")],
                blocks=[],
            )
            for idx in sorted(failed_indices):
                if 0 <= idx < len(paras):
                    uncategorised.blocks.append(paras[idx][0])
            if uncategorised.blocks:
                new_sections.append(uncategorised)

        doc.sections = new_sections
        return doc

    @staticmethod
    def _para_text(para: Paragraph) -> str:
        if para is None or not para.runs:
            return ""
        return " ".join((r.text or "") for r in para.runs if r and r.text).strip()

    @staticmethod
    def _parse_response(raw: str, batch_size: int) -> list[dict] | None:
        """Parse and validate the LLM response. Returns None on failure."""
        text = raw.strip()

        # Strip markdown fences
        fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            _logger.debug("TopicSegmenter: failed to parse JSON: %s", text[:100])
            return None

        if not isinstance(parsed, list):
            _logger.debug("TopicSegmenter: response is not a list")
            return None

        for item in parsed:
            if not isinstance(item, dict):
                return None
            if "start_index" not in item or "end_index" not in item or "topic" not in item:
                _logger.debug("TopicSegmenter: missing required keys in segment: %s", item)
                return None
            if not isinstance(item["start_index"], int) or not isinstance(item["end_index"], int):
                return None

        return parsed

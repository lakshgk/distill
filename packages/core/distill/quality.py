"""
distill.quality
~~~~~~~~~~~~~~~
Quality scoring for Distill conversions.

Computes a quality_score (0.0 – 1.0) by comparing the IR source structure
against the rendered Markdown output. The score reflects how well structural
elements (headings, tables, lists) survived the conversion pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from distill.ir import Document, List, Section, Table


@dataclass
class QualityScore:
    """Breakdown of the conversion quality score."""
    overall:               float           # 0.0 – 1.0 composite score

    heading_preservation:  float = 0.0    # headings in IR vs headings in Markdown
    table_preservation:    float = 0.0    # tables in IR vs GFM tables in Markdown
    list_preservation:     float = 0.0    # lists in IR vs Markdown lists
    token_reduction_ratio: float = 0.0    # tokens(output) / tokens(naive_estimate)
    valid_markdown:        bool  = True   # output is parseable CommonMark

    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.overall >= 0.70

    def __str__(self) -> str:
        status = "PASS" if self.passed else "WARN"
        return (
            f"[{status}] Quality: {self.overall:.2f} "
            f"(headings={self.heading_preservation:.2f}, "
            f"tables={self.table_preservation:.2f}, "
            f"lists={self.list_preservation:.2f})"
        )


def score(ir: Document, markdown: str) -> QualityScore:
    """
    Compute a QualityScore by comparing the IR tree against the Markdown output.

    Args:
        ir:       The source IR Document
        markdown: The rendered Markdown string

    Returns:
        A QualityScore dataclass with per-dimension breakdown and overall score.
    """
    warnings: list[str] = []

    # ── Count IR elements ────────────────────────────────────────────────────
    ir_headings = _count_ir_headings(ir)
    ir_tables   = _count_ir_tables(ir)
    ir_lists    = _count_ir_lists(ir)

    # ── Count Markdown elements ──────────────────────────────────────────────
    md_headings = len(re.findall(r"^#{1,6} .+", markdown, re.MULTILINE))
    md_tables   = len(re.findall(r"^\|.+\|$", markdown, re.MULTILINE))  # rough: count separator rows
    md_lists    = len(re.findall(r"^[\-\*\+] .+|^\d+\. .+", markdown, re.MULTILINE))

    # ── Per-dimension scores ─────────────────────────────────────────────────
    heading_score = _safe_ratio(md_headings, ir_headings)
    table_score   = _safe_ratio(
        # A table with N rows generates N+2 lines (header + sep + rows)
        # so count separator lines only
        len(re.findall(r"^\|[-| :]+\|$", markdown, re.MULTILINE)),
        ir_tables
    )
    list_score    = _safe_ratio(md_lists, ir_lists)

    # ── Token reduction ratio ────────────────────────────────────────────────
    # Naive estimate: count words in Markdown × 1.3 tokens/word (rough GPT tokenizer average)
    # Compare against the estimated token count of a naive text dump (IR word count × 2.5 overhead)
    md_token_est   = len(markdown.split()) * 1.3
    naive_estimate = (ir.metadata.word_count or len(markdown.split())) * 2.5
    token_ratio    = 1.0 - min(md_token_est / max(naive_estimate, 1), 1.0)
    token_ratio    = max(token_ratio, 0.0)

    # ── Validation warnings ──────────────────────────────────────────────────
    if ir_headings > 0 and heading_score < 0.8:
        warnings.append(
            f"Heading preservation low ({heading_score:.0%}): "
            f"{ir_headings} in source, {md_headings} in output"
        )
    if ir_tables > 0 and table_score < 0.8:
        warnings.append(
            f"Table preservation low ({table_score:.0%}): "
            f"{ir_tables} in source"
        )

    # ── Composite score (weighted) ────────────────────────────────────────────
    # Weights: heading 25%, table 25%, list 15%, token_reduction 20%, markdown_valid 15%
    overall = (
        heading_score * 0.25 +
        table_score   * 0.25 +
        list_score    * 0.15 +
        token_ratio   * 0.20 +
        1.0           * 0.15  # assume valid markdown unless we add a linter
    )

    return QualityScore(
        overall               = round(overall, 3),
        heading_preservation  = round(heading_score, 3),
        table_preservation    = round(table_score, 3),
        list_preservation     = round(list_score, 3),
        token_reduction_ratio = round(token_ratio, 3),
        valid_markdown        = True,
        warnings              = warnings,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _count_ir_headings(doc: Document) -> int:
    count = 0
    def _walk(sections):
        nonlocal count
        for s in sections:
            if s.heading:
                count += 1
            _walk(s.subsections)
    _walk(doc.sections)
    return count


def _count_ir_tables(doc: Document) -> int:
    count = 0
    def _walk_blocks(blocks):
        nonlocal count
        for b in blocks:
            if isinstance(b, Table):
                count += 1
    def _walk_sections(sections):
        for s in sections:
            _walk_blocks(s.blocks)
            _walk_sections(s.subsections)
    _walk_sections(doc.sections)
    return count


def _count_ir_lists(doc: Document) -> int:
    count = 0
    def _walk_blocks(blocks):
        nonlocal count
        for b in blocks:
            if isinstance(b, List):
                count += len(b.items)
    def _walk_sections(sections):
        for s in sections:
            _walk_blocks(s.blocks)
            _walk_sections(s.subsections)
    _walk_sections(doc.sections)
    return count


def _safe_ratio(actual: int, expected: int) -> float:
    if expected == 0:
        return 1.0  # nothing expected, nothing missing → perfect
    return min(actual / expected, 1.0)

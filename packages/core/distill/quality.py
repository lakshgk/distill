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

from distill.ir import Document, List, Paragraph, Section, Table, TextRun


@dataclass
class QualityScore:
    """Breakdown of the conversion quality score."""
    overall:               Optional[float]  = None    # 0.0 – 1.0 composite score, None when gate fires

    heading_preservation:  float = 0.0    # headings in IR vs headings in Markdown
    table_preservation:    float = 0.0    # tables in IR vs GFM tables in Markdown
    list_preservation:     float = 0.0    # lists in IR vs Markdown lists
    token_reduction_ratio: float = 0.0    # tokens(output) / tokens(naive_estimate)
    valid_markdown:        bool  = True   # output is parseable CommonMark

    warnings: list[str] = field(default_factory=list)
    error:    Optional[str]  = None       # set when pre-check gate fires
    components: Optional[dict] = None     # per-metric breakdown, None when gate fires

    @property
    def passed(self) -> bool:
        if self.overall is None:
            return False
        return self.overall >= 0.70

    def __str__(self) -> str:
        if self.overall is None:
            return f"[FAIL] Quality: N/A — {self.error}"
        status = "PASS" if self.passed else "WARN"
        return (
            f"[{status}] Quality: {self.overall:.2f} "
            f"(headings={self.heading_preservation:.2f}, "
            f"tables={self.table_preservation:.2f}, "
            f"lists={self.list_preservation:.2f})"
        )

    @classmethod
    def score(cls, ir: Document, markdown: str = "", *, outcome=None) -> "QualityScore":
        """
        Compute a QualityScore by comparing the IR tree against the Markdown output.

        Pre-check gate: if outcome is not SUCCESS, or if the IR is empty,
        returns immediately with overall=None and an error message.
        """
        from distill import ParserOutcome

        if outcome is None:
            outcome = ParserOutcome.SUCCESS

        # ── Pre-check gate ───────────────────────────────────────────────────
        _OUTCOME_ERRORS = {
            ParserOutcome.OCR_REQUIRED: "OCR is required but not enabled",
            ParserOutcome.EMPTY_IR: "No content extracted from document",
            ParserOutcome.PARSE_ERROR: "Document parsing failed",
        }
        if outcome != ParserOutcome.SUCCESS:
            return cls(overall=None, error=_OUTCOME_ERRORS.get(outcome, str(outcome)), components=None)

        # Empty IR gate — no sections, or all sections have zero text content
        if _ir_is_empty(ir):
            return cls(overall=None, error="No content extracted from document", components=None)

        # ── Delegate to the metric computation ───────────────────────────────
        return _compute_metrics(ir, markdown)


def score(ir: Document, markdown: str = "", *, outcome=None) -> QualityScore:
    """
    Module-level convenience wrapper around QualityScore.score().
    """
    return QualityScore.score(ir, markdown, outcome=outcome)


def _ir_is_empty(ir: Document) -> bool:
    """Return True if the IR has no sections or every section has zero text content."""
    if not ir.sections:
        return True
    for section in ir.sections:
        for block in section.blocks:
            if isinstance(block, Paragraph):
                for run in block.runs:
                    if isinstance(run, TextRun) and run.text and run.text.strip():
                        return False
            elif isinstance(block, Table):
                return False
    return True


def _compute_metrics(ir: Document, markdown: str) -> QualityScore:
    """
    Compute quality metrics by comparing the IR tree against the Markdown output.
    Called only after the pre-check gate passes.
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
    # Only compute when the source word count is known. When word_count is None
    # (scanned PDFs, parsers that could not count source words), token_ratio is
    # set to None and excluded from the composite score.
    source_wc = ir.metadata.word_count
    if source_wc is not None and source_wc > 0:
        md_token_est   = len(markdown.split()) * 1.3
        naive_estimate = source_wc * 2.5
        token_ratio: Optional[float] = 1.0 - min(md_token_est / max(naive_estimate, 1), 1.0)
        token_ratio = max(token_ratio, 0.0)
    else:
        token_ratio = None

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
    # Base weights: heading 25%, table 25%, list 15%, token_reduction 20%, valid_md 15%
    # When token_ratio is unavailable, its 20% weight is redistributed proportionally
    # across the remaining four metrics so the composite still sums to 1.0:
    #   heading: 25/80 = 0.3125, table: 25/80 = 0.3125,
    #   list: 15/80 = 0.1875, valid_md: 15/80 = 0.1875
    if token_ratio is not None:
        overall = (
            heading_score * 0.25 +
            table_score   * 0.25 +
            list_score    * 0.15 +
            token_ratio   * 0.20 +
            1.0           * 0.15  # assume valid markdown unless we add a linter
        )
    else:
        overall = (
            heading_score * 0.3125 +
            table_score   * 0.3125 +
            list_score    * 0.1875 +
            1.0           * 0.1875  # assume valid markdown unless we add a linter
        )

    return QualityScore(
        overall               = round(overall, 3),
        heading_preservation  = round(heading_score, 3),
        table_preservation    = round(table_score, 3),
        list_preservation     = round(list_score, 3),
        token_reduction_ratio = round(token_ratio, 3) if token_ratio is not None else 0.0,
        valid_markdown        = True,
        warnings              = warnings,
        components            = {
            "heading_preservation":  round(heading_score, 3),
            "table_preservation":    round(table_score, 3),
            "list_preservation":     round(list_score, 3),
            "token_ratio":           round(token_ratio, 3) if token_ratio is not None else None,
            "valid_markdown":        True,
        },
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

"""
distill
~~~~~~~
Convert any document format to clean, LLM-optimized Markdown.

Quick start:
    from distill import convert

    result = convert("report.docx")
    print(result.markdown)
    print(result.quality_score)

Power users (IR access):
    from distill import convert_to_ir
    ir = convert_to_ir("report.pdf")
    # manipulate the IR tree...
    markdown = ir.render()

Introspection:
    from distill import registry
    registry.supported_formats()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Union

from distill.ir import Document, DocumentMetadata
from distill.parsers.base import ParseOptions
from distill.registry import registry


@dataclass
class ConversionResult:
    """The result of a convert() call."""
    markdown:      str
    quality_score: float
    metadata:      DocumentMetadata
    warnings:      list[str]           = field(default_factory=list)
    ir:            Optional[Document]  = None   # populated if return_ir=True


def convert(
    source:           Union[str, Path, bytes],
    *,
    return_ir:        bool                    = False,
    include_metadata: bool                    = False,
    options:          Optional[ParseOptions]  = None,
    **kwargs,
) -> ConversionResult:
    """
    Convert a document to Markdown in one call.

    Args:
        source:           File path, Path object, or raw bytes.
                          For Google Workspace files, pass the Drive URL and set
                          options.extra['access_token'].
        return_ir:        If True, attach the IR Document to ConversionResult.ir.
        include_metadata: If True, emit a YAML front-matter block with document
                          metadata at the top of the Markdown output.
                          Defaults to False — metadata is opt-in.
        options:          ParseOptions for fine-grained control.
        **kwargs:         Shorthand for common ParseOptions fields:
                          images, max_table_rows, image_dir, vision_provider, streaming

    Returns:
        ConversionResult with markdown, quality_score, metadata, and warnings.

    Raises:
        UnsupportedFormatError: format has no available parser
        ParseError:             parsing failed
    """
    if options is None:
        options = ParseOptions(**{k: v for k, v in kwargs.items()
                                  if hasattr(ParseOptions, k)})

    # Find and run parser
    parser_cls = registry.find(source)
    parser     = parser_cls()
    ir         = parser.parse(source, options)

    # Vision captioning (opt-in; no-op if distill-core[vision] not installed)
    if options.images == "caption" and options.vision_provider:
        from distill.parsers._vision import caption_images
        caption_images(ir, options)

    # Render IR → Markdown
    markdown = ir.render(front_matter=include_metadata)

    # Score quality
    from distill.quality import score as _score
    qs = _score(ir, markdown)

    return ConversionResult(
        markdown      = markdown,
        quality_score = qs.overall,
        metadata      = ir.metadata,
        warnings      = ir.warnings + qs.warnings,
        ir            = ir if return_ir else None,
    )


def convert_stream(
    source:           Union[str, Path, bytes],
    *,
    include_metadata: bool                   = False,
    options:          Optional[ParseOptions] = None,
    **kwargs,
) -> Iterator[str]:
    """
    Parse a document and yield Markdown chunks one section at a time.

    Yields:
        str: Rendered Markdown. If include_metadata=True, the first chunk is
             the YAML front-matter block. Each subsequent chunk is one
             top-level section.

    Raises:
        UnsupportedFormatError: format has no available parser
        ParseError:             parsing failed
    """
    if options is None:
        options = ParseOptions(**{k: v for k, v in kwargs.items()
                                  if hasattr(ParseOptions, k)})
    from distill.renderer import MarkdownRenderer
    parser_cls = registry.find(source)
    ir         = parser_cls().parse(source, options)

    # Vision captioning (opt-in; no-op if distill-core[vision] not installed)
    if options.images == "caption" and options.vision_provider:
        from distill.parsers._vision import caption_images
        caption_images(ir, options)

    yield from MarkdownRenderer(front_matter=include_metadata).render_stream(ir)


def convert_to_ir(
    source:  Union[str, Path, bytes],
    options: Optional[ParseOptions] = None,
    **kwargs,
) -> Document:
    """
    Parse a document and return the raw IR Document tree.
    Useful for manipulating structure before rendering.
    """
    if options is None:
        options = ParseOptions(**{k: v for k, v in kwargs.items()
                                  if hasattr(ParseOptions, k)})
    parser_cls = registry.find(source)
    return parser_cls().parse(source, options)


__all__ = [
    "convert",
    "convert_stream",
    "convert_to_ir",
    "registry",
    "ConversionResult",
    "ParseOptions",
    "Document",
]

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

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Union


class ParserOutcome(enum.Enum):
    SUCCESS = "success"
    EMPTY_IR = "empty_ir"
    OCR_REQUIRED = "ocr_required"
    PARSE_ERROR = "parse_error"

from distill.ir import Document, DocumentMetadata
from distill.parsers.base import ParseOptions
from distill.quality import QualityScore
from distill.registry import registry

import distill.parsers.html         # noqa: F401 — triggers @registry.register for HTMLParser

# Optional parsers — import errors are swallowed when extras are not installed.
# The registry skips parsers whose requires are missing, but the import itself
# must not crash the package for users who only need core formats.
for _mod in (
    "distill.parsers.audio",
    "distill.parsers.epub",
    "distill.parsers.wsdl",
    "distill.parsers.json_parser",
    "distill.parsers.sql",
):
    try:
        __import__(_mod)
    except ImportError:
        pass


@dataclass
class ConversionResult:
    """The result of a convert() call."""
    markdown:            str
    quality_score:       Optional[float]
    metadata:            DocumentMetadata
    warnings:            list[str]              = field(default_factory=list)
    ir:                  Optional[Document]     = None   # populated if return_ir=True
    quality_details:     Optional[QualityScore] = None   # full per-metric breakdown
    structured_warnings: list[dict]             = field(default_factory=list)
    chunks:              Optional[list]         = None   # populated when output_format="chunks"
    document_json:       Optional[dict]         = None   # populated when output_format="json"
    html:                Optional[str]          = None   # populated when output_format="html"
    extracted:           Optional[dict]         = None   # populated when extract=True
    parser_outcome:      ParserOutcome          = ParserOutcome.SUCCESS


_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}


def convert(
    source:           Union[str, Path, bytes],
    *,
    return_ir:        bool                    = False,
    include_metadata: bool                    = False,
    options:          Optional[ParseOptions]  = None,
    _async_context:   bool                    = False,
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
        _async_context:   Internal flag — set True by the Celery worker to bypass
                          the audio sync guard. Callers should not set this.
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

    # Audio sync guard — audio must be processed via the async worker
    if not _async_context and not isinstance(source, bytes):
        ext = Path(str(source)).suffix.lower()
        if ext in _AUDIO_EXTENSIONS:
            from distill.parsers.base import ParseError
            raise ParseError(
                "Audio conversion requires async processing. "
                "Submit via POST /api/convert — the file will be queued automatically."
            )

    # Create a warning collector and attach it so parsers can use it
    from distill.warnings import WarningCollector
    collector = WarningCollector()
    options.collector = collector

    # Find and run parser
    parser_cls = registry.find(source)
    parser     = parser_cls()
    ir         = parser.parse(source, options)

    # Vision captioning (opt-in; no-op if distill-core[vision] not installed)
    if options.images == "caption" and options.vision_provider:
        from distill.parsers._vision import caption_images
        caption_images(ir, options)

    # Cross-page table detection + optional LLM merge (PDF only)
    source_fmt = getattr(ir.metadata, "source_format", None) or ""
    if source_fmt.lower() == "pdf":
        from distill.features.table_merge import TableFragmentDetector, TableMerger
        detector = TableFragmentDetector()
        pairs = detector.detect(ir, collector)

        if options.llm_merge_tables:
            if options.llm is None:
                from distill.parsers.base import ParseError
                raise ParseError(
                    "llm_merge_tables requires llm_api_key and llm_model to be set"
                )
            from distill.features.llm import LLMClient
            llm_client = LLMClient(options.llm)
            merger = TableMerger(llm_client)
            ir = merger.merge(ir, pairs)

    # Audio topic segmentation (opt-in, audio only)
    if options.topic_segmentation and source_fmt.lower() == "audio":
        if options.llm is None:
            from distill.parsers.base import ParseError
            raise ParseError(
                "topic_segmentation=True requires llm_api_key and llm_model "
                "to be set. Provide them as form fields or set "
                "DISTILL_LLM_API_KEY and DISTILL_LLM_MODEL in your environment."
            )
        from distill.features.topic_segment import TopicSegmenter
        from distill.features.llm import LLMClient as _LLMClient
        segmenter = TopicSegmenter(_LLMClient(options.llm))
        ir = segmenter.segment(ir)

    # Render IR → Markdown
    markdown = ir.render(front_matter=include_metadata)

    # Structured JSON extraction (opt-in via extract=True)
    extracted_data = None
    if options.extract:
        from distill.parsers.base import ParseError as _PE
        if not options.schema or not isinstance(options.schema, dict):
            raise _PE("extract=True requires a non-empty schema dict")
        if options.llm is None:
            raise _PE("extract=True requires llm_api_key and llm_model to be set")
        from distill.features.llm import LLMClient
        from distill.features.json_extract import JSONExtractor, ExtractionError
        try:
            extractor = JSONExtractor(LLMClient(options.llm))
            extracted_data = extractor.extract(markdown, options.schema)
        except ExtractionError as exc:
            raise _PE(f"Structured extraction failed: {exc}") from exc

    # Determine parser outcome — parsers may set it on the Document
    outcome = getattr(ir, "parser_outcome", None) or ParserOutcome.SUCCESS

    # Score quality
    from distill.quality import score as _score
    qs = _score(ir, markdown, outcome=outcome)

    # Serialise structured warnings; fall back to empty list on any error
    try:
        structured_warnings = collector.to_dict()
    except Exception:
        structured_warnings = []

    # Route alternate output formats
    chunks_out    = None
    document_json = None

    if options.output_format == "chunks":
        from distill.renderers.chunks import ChunksRenderer
        source_name = str(source) if not isinstance(source, bytes) else "<bytes>"
        fmt         = getattr(ir.metadata, "source_format", "") or ""
        chunks_out  = ChunksRenderer().render(ir, source_document=source_name, source_format=fmt)

    elif options.output_format == "json":
        from distill.renderers.json_renderer import JSONRenderer
        document_json = JSONRenderer().render(ir)

    html_out = None
    if options.output_format == "html":
        from distill.renderers.html_renderer import HTMLRenderer
        html_out = HTMLRenderer().render(ir)

    return ConversionResult(
        markdown             = markdown,
        quality_score        = qs.overall,
        quality_details      = qs,
        metadata             = ir.metadata,
        warnings             = ir.warnings + qs.warnings,
        structured_warnings  = structured_warnings,
        ir                   = ir if return_ir else None,
        chunks               = chunks_out,
        document_json        = document_json,
        html                 = html_out,
        extracted            = extracted_data,
        parser_outcome       = outcome,
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
    "ParserOutcome",
    "ParseOptions",
    "QualityScore",
    "Document",
]

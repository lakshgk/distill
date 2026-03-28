"""
distill_app.ui
~~~~~~~~~~~~~~
Gradio UI for Distill.

Run:  python -m distill_app
      distill-app
"""

from __future__ import annotations

import tempfile
from pathlib import Path

SUPPORTED_EXTENSIONS = [
    ".docx", ".doc", ".xlsx", ".xls", ".csv",
    ".pptx", ".ppt", ".pdf",
]

_FORMAT_LABELS = {
    ".docx": "Word",            ".doc":  "Word (legacy)",
    ".xlsx": "Excel",           ".xls":  "Excel (legacy)",  ".csv": "CSV",
    ".pptx": "PowerPoint",      ".ppt":  "PowerPoint (legacy)",
    ".pdf":  "PDF",
}


# ── Quality badge ─────────────────────────────────────────────────────────────

def quality_badge(qs) -> str:
    """
    Render a quality badge with a hover tooltip showing per-metric breakdown.

    qs: QualityScore object (preferred) or plain float (fallback).
    """
    try:
        from distill.quality import QualityScore as _QS
        is_qs = isinstance(qs, _QS)
    except ImportError:
        is_qs = False

    overall = qs.overall if is_qs else float(qs)

    if overall >= 0.85:
        color, label = "#27ae60", "Excellent"
    elif overall >= 0.70:
        color, label = "#f39c12", "Good"
    else:
        color, label = "#e74c3c", "Low"
    pct = int(overall * 100)

    badge_inner = f"Quality: {pct}% \u2014 {label}" + ("  \u24d8" if is_qs else "")
    badge_span  = (
        f'<span style="background:{color};color:#fff;padding:4px 12px;'
        f'border-radius:12px;font-weight:600;font-size:13px;cursor:default">'
        f'{badge_inner}</span>'
    )

    if not is_qs:
        return badge_span

    details = [
        ("Headings",   qs.heading_preservation),
        ("Tables",     qs.table_preservation),
        ("Lists",      qs.list_preservation),
        ("Efficiency", qs.token_reduction_ratio),
    ]
    rows = "".join(
        f'<tr>'
        f'<td style="padding:3px 12px 3px 0;color:#bbb;font-size:12px">{name}</td>'
        f'<td style="font-size:12px;font-weight:600;'
        f'color:{"#4caf50" if v >= 0.80 else "#ef5350"}">'
        f'{"&#10003;" if v >= 0.80 else "&#9888;"} {int(v * 100)}%</td>'
        f'</tr>'
        for name, v in details
    )

    return (
        '<style>.distill-q:hover .distill-tip{display:block!important}</style>'
        '<div class="distill-q" style="position:relative;display:inline-block">'
        + badge_span +
        '<div class="distill-tip" style="display:none;position:absolute;'
        'bottom:130%;left:0;background:#1e1e1e;color:#eee;border-radius:8px;'
        'padding:10px 14px;box-shadow:0 4px 16px rgba(0,0,0,.4);'
        'z-index:9999;white-space:nowrap;pointer-events:none">'
        '<div style="font-size:11px;color:#888;margin-bottom:6px">Conversion breakdown</div>'
        f'<table style="border-collapse:collapse">{rows}</table>'
        '</div></div>'
    )


# ── Stats bar ─────────────────────────────────────────────────────────────────

def _stats_html(metadata) -> str:
    parts = []
    wc = getattr(metadata, "word_count", None)
    if isinstance(wc, int) and wc > 0:
        parts.append(f"~{wc:,} words")
    pc = getattr(metadata, "page_count", None)
    sc = getattr(metadata, "slide_count", None)
    shc = getattr(metadata, "sheet_count", None)
    if isinstance(pc, int) and pc > 0:
        parts.append(f"{pc} page{'s' if pc != 1 else ''}")
    elif isinstance(sc, int) and sc > 0:
        parts.append(f"{sc} slides")
    elif isinstance(shc, int) and shc > 0:
        parts.append(f"{shc} sheets")
    fmt = getattr(metadata, "source_format", None)
    if fmt:
        parts.append(str(fmt).upper())
    if not parts:
        return ""
    return (
        '<div style="margin-top:6px;font-size:12px;color:#888">'
        + " &middot; ".join(parts)
        + "</div>"
    )


# ── File info strip ───────────────────────────────────────────────────────────

def show_file_info(file_obj):
    """Return gr.update for the file info strip shown after upload."""
    import gradio as gr
    if file_obj is None:
        return gr.update(value="", visible=False)
    path = Path(file_obj.name)
    ext  = path.suffix.lower()
    fmt  = _FORMAT_LABELS.get(ext, ext.lstrip(".").upper())
    try:
        size_bytes = path.stat().st_size
        if size_bytes < 1024:
            size_str = f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            size_str = f"{size_bytes / 1024:.1f} KB"
        else:
            size_str = f"{size_bytes / 1024**2:.1f} MB"
    except OSError:
        size_str = ""
    parts = [f"<b>{path.name}</b>", fmt]
    if size_str:
        parts.append(size_str)
    html = (
        '<div style="font-size:12px;color:#555;padding:4px 2px">'
        "&#128196; " + " &nbsp;&middot;&nbsp; ".join(parts) +
        "</div>"
    )
    return gr.update(value=html, visible=True)


# ── Conversion logic ──────────────────────────────────────────────────────────

def convert_file(file_obj, include_front_matter: bool, max_rows: int):
    """
    Convert an uploaded file to Markdown.

    Returns a 6-tuple:
        (markdown, preview_md, badge_html, stats_html, warnings_update, download_path)
    download_path is the path to a temp .md file, or None on error/no file.
    """
    import gradio as gr
    from distill.parsers.base import DistillError

    _no_warn = gr.update(value="", visible=False)

    if file_obj is None:
        return "", "", "<em>No file uploaded.</em>", "", _no_warn, None

    path = Path(file_obj.name)
    ext  = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        badge = f"<span style='color:red'>Unsupported format: <code>{ext}</code></span>"
        return "", "", badge, "", _no_warn, None

    try:
        from distill import convert, ParseOptions
        options = ParseOptions(max_table_rows=max_rows)
        result  = convert(path, include_metadata=include_front_matter, options=options)

        qs    = getattr(result, "quality_details", None) or result.quality_score
        badge = quality_badge(qs)
        stats = _stats_html(result.metadata)

        if result.warnings:
            warn_str     = "\n".join(f"\u26a0 {w}" for w in result.warnings)
            warnings_upd = gr.update(value=warn_str, visible=True)
        else:
            warnings_upd = _no_warn

        tmp = tempfile.NamedTemporaryFile(
            suffix=".md", prefix=f"{path.stem}_", delete=False
        )
        tmp.write(result.markdown.encode("utf-8"))
        tmp.close()

        return result.markdown, result.markdown, badge, stats, warnings_upd, tmp.name

    except DistillError as e:
        badge = f"<span style='color:red'>Conversion error: {e}</span>"
        return "", "", badge, "", _no_warn, None
    except Exception as e:
        badge = f"<span style='color:red'>Unexpected error: {e}</span>"
        return "", "", badge, "", _no_warn, None


# ── Gradio layout ─────────────────────────────────────────────────────────────

def build_ui():
    import gradio as gr

    with gr.Blocks(title="Distill") as demo:

        gr.Markdown("""
# ⚗️ Distill
**Convert any document to clean, LLM-optimized Markdown**

Supports: Word (.docx / .doc), Excel (.xlsx / .xls / .csv), PowerPoint (.pptx / .ppt), PDF
        """)

        with gr.Row():

            # ── Left column: controls ────────────────────────────────────────
            with gr.Column(scale=1):
                file_info_out = gr.HTML(value="", visible=False)
                file_input    = gr.File(
                    label      = "Drop your document here",
                    file_types = SUPPORTED_EXTENSIONS,
                )
                with gr.Accordion("Options", open=False):
                    front_matter = gr.Checkbox(
                        label = "Include Metadata",
                        value = True,
                    )
                    max_rows = gr.Slider(
                        label   = "Max table rows",
                        minimum = 10, maximum = 5000, value = 500, step = 10,
                    )
                with gr.Row():
                    convert_btn = gr.Button("Convert", variant="primary", size="lg")
                    clear_btn   = gr.Button("Clear", size="lg")

            # ── Right column: output ─────────────────────────────────────────
            with gr.Column(scale=2):
                quality_badge_out = gr.HTML(label="Quality")
                stats_out         = gr.HTML()

                with gr.Tabs():
                    with gr.Tab("Markdown"):
                        markdown_out = gr.Code(
                            label    = "Markdown output",
                            language = "markdown",
                            lines    = 30,
                        )
                    with gr.Tab("Preview"):
                        preview_out = gr.Markdown(
                            label = "Rendered preview",
                            value = "",
                        )

                warnings_out = gr.Textbox(
                    label       = "Warnings",
                    lines       = 3,
                    visible     = False,
                    interactive = False,
                )
                download_btn = gr.DownloadButton(
                    label = "Download .md",
                    value = None,
                )

        # ── Events ───────────────────────────────────────────────────────────

        file_input.change(
            fn      = show_file_info,
            inputs  = [file_input],
            outputs = [file_info_out],
        )

        convert_btn.click(
            fn      = convert_file,
            inputs  = [file_input, front_matter, max_rows],
            outputs = [markdown_out, preview_out, quality_badge_out,
                       stats_out, warnings_out, download_btn],
        )

        def _clear():
            import gradio as gr
            no_warn = gr.update(value="", visible=False)
            return "", "", "", "", no_warn, None, gr.update(value="", visible=False)

        clear_btn.click(
            fn      = _clear,
            inputs  = [],
            outputs = [markdown_out, preview_out, quality_badge_out,
                       stats_out, warnings_out, download_btn, file_info_out],
        )

    return demo


def launch(**kwargs):
    import gradio as gr
    demo = build_ui()
    demo.launch(
        server_name = kwargs.get("host", "127.0.0.1"),
        server_port = kwargs.get("port", 7860),
        share       = kwargs.get("share", False),
        inbrowser   = kwargs.get("inbrowser", True),
        theme       = gr.themes.Soft(primary_hue="blue"),
        css         = ".output-markdown { font-family: monospace; font-size: 13px; }",
    )

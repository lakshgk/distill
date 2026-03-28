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


# ── Conversion logic (module-level for testability) ───────────────────────────

def quality_badge(score: float) -> str:
    if score >= 0.85:
        color, label = "#27ae60", "Excellent"
    elif score >= 0.70:
        color, label = "#f39c12", "Good"
    else:
        color, label = "#e74c3c", "Low"
    pct = int(score * 100)
    return (
        f'<span style="background:{color};color:#fff;padding:4px 10px;'
        f'border-radius:12px;font-weight:600;font-size:13px">'
        f'Quality: {pct}% \u2014 {label}</span>'
    )


def convert_file(file_obj, include_front_matter: bool, max_rows: int):
    """
    Convert an uploaded file to Markdown.

    Returns a 4-tuple: (markdown, badge_html, warnings, download_path).
    download_path is the path to a temp .md file, or None on error.
    """
    from distill.parsers.base import DistillError

    if file_obj is None:
        return "", "<em>No file uploaded.</em>", "", None

    path = Path(file_obj.name)
    ext  = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        return (
            "",
            f"<span style='color:red'>Unsupported format: <code>{ext}</code></span>",
            "",
            None,
        )

    try:
        from distill import convert, ParseOptions
        options = ParseOptions(max_table_rows=max_rows)
        result  = convert(path, include_metadata=include_front_matter, options=options)
        badge    = quality_badge(result.quality_score)
        warnings = ""
        if result.warnings:
            warnings = "\n".join(f"\u26a0 {w}" for w in result.warnings)

        # Write markdown to a named temp file for download
        tmp = tempfile.NamedTemporaryFile(
            suffix=".md", prefix=f"{path.stem}_", delete=False
        )
        tmp.write(result.markdown.encode("utf-8"))
        tmp.close()

        return result.markdown, badge, warnings, tmp.name

    except DistillError as e:
        return "", f"<span style='color:red'>Conversion error: {e}</span>", "", None
    except Exception as e:
        return "", f"<span style='color:red'>Unexpected error: {e}</span>", "", None


# ── Gradio layout ─────────────────────────────────────────────────────────────

def build_ui():
    import gradio as gr

    with gr.Blocks(title="Distill") as demo:

        gr.Markdown("""
# Distill
**Convert any document to clean, LLM-optimized Markdown**

Supports: Word (.docx), Excel (.xlsx, .csv), PowerPoint (.pptx), PDF
        """)

        with gr.Row():
            with gr.Column(scale=1):
                file_input = gr.File(
                    label="Drop your document here",
                    file_types=SUPPORTED_EXTENSIONS,
                )
                with gr.Accordion("Options", open=False):
                    front_matter = gr.Checkbox(label="Include YAML front-matter", value=True)
                    max_rows     = gr.Slider(
                        label="Max table rows",
                        minimum=10, maximum=5000, value=500, step=10,
                    )
                convert_btn = gr.Button("Convert", variant="primary", size="lg")

            with gr.Column(scale=2):
                quality_badge_out = gr.HTML(label="Quality")
                markdown_out      = gr.Code(
                    label    = "Markdown output",
                    language = "markdown",
                    lines    = 30,
                )
                warnings_out = gr.Textbox(
                    label       = "Warnings",
                    lines       = 3,
                    visible     = True,
                    interactive = False,
                )
                download_btn = gr.DownloadButton(
                    label = "Download .md",
                    value = None,
                )

        convert_btn.click(
            fn      = convert_file,
            inputs  = [file_input, front_matter, max_rows],
            outputs = [markdown_out, quality_badge_out, warnings_out, download_btn],
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

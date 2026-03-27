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


def build_ui():
    import gradio as gr
    from distill import convert
    from distill.parsers.base import DistillError

    SUPPORTED_EXTENSIONS = [
        ".docx", ".doc", ".xlsx", ".xls", ".csv",
        ".pptx", ".ppt", ".pdf",
    ]

    def _quality_badge(score: float) -> str:
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
            f'Quality: {pct}% — {label}</span>'
        )

    def convert_file(file_obj, include_front_matter: bool, max_rows: int):
        if file_obj is None:
            return "", "<em>No file uploaded.</em>", ""

        path = Path(file_obj.name)
        ext  = path.suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            return (
                "",
                f"<span style='color:red'>Unsupported format: <code>{ext}</code></span>",
                "",
            )

        try:
            from distill import convert, ParseOptions
            options  = ParseOptions(max_table_rows=max_rows)
            result   = convert(path, options=options)
            badge    = _quality_badge(result.quality_score)
            warnings = ""
            if result.warnings:
                warnings = "\n".join(f"⚠ {w}" for w in result.warnings)
            return result.markdown, badge, warnings

        except DistillError as e:
            return "", f"<span style='color:red'>Conversion error: {e}</span>", ""
        except Exception as e:
            return "", f"<span style='color:red'>Unexpected error: {e}</span>", ""

    # ── Layout ────────────────────────────────────────────────────────────────
    with gr.Blocks(
        title="Distill",
        theme=gr.themes.Soft(primary_hue="blue"),
        css=".output-markdown { font-family: monospace; font-size: 13px; }",
    ) as demo:

        gr.Markdown("""
# ⚗️ Distill
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
                quality_badge = gr.HTML(label="Quality")
                markdown_out  = gr.Code(
                    label    = "Markdown output",
                    language = "markdown",
                    lines    = 30,
                )
                warnings_out = gr.Textbox(
                    label    = "Warnings",
                    lines    = 3,
                    visible  = True,
                    interactive = False,
                )
                gr.DownloadButton  # placeholder — wired below

        convert_btn.click(
            fn      = convert_file,
            inputs  = [file_input, front_matter, max_rows],
            outputs = [markdown_out, quality_badge, warnings_out],
        )

    return demo


def launch(**kwargs):
    demo = build_ui()
    demo.launch(
        server_name = kwargs.get("host", "127.0.0.1"),
        server_port = kwargs.get("port", 7860),
        share       = kwargs.get("share", False),
        inbrowser   = kwargs.get("inbrowser", True),
    )

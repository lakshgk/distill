"""
distill_app.server
~~~~~~~~~~~~~~~~~~
FastAPI server for Distill.

Routes:
    GET  /                  → serves static/index.html
    POST /api/convert       → convert uploaded file, return JSON
"""

from __future__ import annotations

import tempfile
import webbrowser
from pathlib import Path
from threading import Timer
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"


def build_app():
    app = FastAPI(title="Distill", version="0.1.0")

    # ── Static files ──────────────────────────────────────────────────────────

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    if (STATIC_DIR).exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ── Convert endpoint ──────────────────────────────────────────────────────

    @app.post("/api/convert")
    async def convert(
        file:             UploadFile       = File(...),
        include_metadata: bool             = Form(True),
        max_rows:         int              = Form(500),
        enable_ocr:       bool             = Form(False),
    ):
        from distill import convert as _convert, ParseOptions
        from distill.parsers.base import DistillError

        SUPPORTED = {".docx", ".doc", ".xlsx", ".xls", ".csv",
                     ".pptx", ".ppt", ".pdf"}

        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in SUPPORTED:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported format: '{suffix}'. "
                       f"Supported: {', '.join(sorted(SUPPORTED))}",
            )

        # Write upload to a temp file so parsers get a real path + extension
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = Path(tmp.name)

        try:
            options = ParseOptions(
                max_table_rows=max_rows,
                extra={"enable_ocr": enable_ocr},
            )
            result = _convert(
                tmp_path,
                include_metadata=include_metadata,
                options=options,
            )
        except DistillError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")
        finally:
            tmp_path.unlink(missing_ok=True)

        # Build quality breakdown
        qs = result.quality_details
        quality = {"overall": round(result.quality_score, 3)}
        if qs is not None:
            quality.update({
                "headings":   round(qs.heading_preservation, 3),
                "tables":     round(qs.table_preservation, 3),
                "lists":      round(qs.list_preservation, 3),
                "efficiency": round(qs.token_reduction_ratio, 3),
            })

        # Build stats
        meta  = result.metadata
        stats = {
            "words":  getattr(meta, "word_count",  None),
            "pages":  getattr(meta, "page_count",  None),
            "slides": getattr(meta, "slide_count", None),
            "sheets": getattr(meta, "sheet_count", None),
            "format": (getattr(meta, "source_format", None) or "").upper() or None,
        }

        return JSONResponse({
            "markdown": result.markdown,
            "quality":  quality,
            "stats":    stats,
            "warnings": result.warnings,
        })

    return app


def launch(
    host:      str  = "127.0.0.1",
    port:      int  = 7860,
    inbrowser: bool = True,
):
    import uvicorn

    app = build_app()

    if inbrowser:
        url = f"http://{host}:{port}"
        Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")

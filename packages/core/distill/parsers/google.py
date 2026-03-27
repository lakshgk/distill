"""
distill.parsers.google
~~~~~~~~~~~~~~~~~~~~~~
Parser for Google Workspace documents via the Google Drive Export API.

Strategy:
  Google Docs   → Export as .docx → DocxParser pipeline
                  OR export as text/markdown (native, when available)
  Google Sheets → Export as .xlsx → XlsxParser pipeline
  Google Slides → Export as .pptx → PptxParser pipeline

Authentication:
  Accepts an OAuth2 access_token or service account credentials.
  Token management is the caller's responsibility — Distill is stateless.

Install:
    pip install distill-core[google]
    # adds: google-api-python-client, google-auth
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Optional, Union

from distill.ir import Document, DocumentMetadata
from distill.parsers.base import ParseError, ParseOptions, Parser, UnsupportedFormatError
from distill.registry import registry


# Google Drive export MIME types
_EXPORT_MAP = {
    # Google Docs → DOCX
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    # Google Sheets → XLSX
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    # Google Slides → PPTX
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}

_FILE_ID_RE = re.compile(r"/d/([a-zA-Z0-9_-]{10,})")


@registry.register
class GoogleDocsParser(Parser):
    """
    Fetches a Google Workspace document via Drive API and converts it
    by delegating to the appropriate format-specific parser.
    """

    # Google Drive URLs are passed as "source" strings
    extensions = [".gdoc", ".gsheet", ".gslides"]
    mime_types = list(_EXPORT_MAP.keys())
    requires   = ["googleapiclient", "google.auth"]

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()

        access_token = options.extra.get("access_token")
        credentials  = options.extra.get("credentials")  # google.oauth2.credentials.Credentials

        if not access_token and not credentials:
            raise ParseError(
                "Google Workspace parsing requires an OAuth2 access_token or credentials. "
                "Pass via: convert(url, extra={'access_token': '...'})"
            )

        file_id = self._extract_file_id(str(source))
        if not file_id:
            raise ParseError(f"Could not extract Google Drive file ID from: {source!r}")

        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload
            import google.oauth2.credentials as ga_credentials

            if access_token and not credentials:
                credentials = ga_credentials.Credentials(token=access_token)

            service = build("drive", "v3", credentials=credentials)

            # Get file metadata to determine type
            file_meta = service.files().get(fileId=file_id, fields="mimeType,name").execute()
            mime_type  = file_meta.get("mimeType", "")
            file_name  = file_meta.get("name", "document")

        except ImportError as e:
            raise ParseError(
                f"Google API client not available: {e}. "
                f"Install with: pip install distill-core[google]"
            ) from e
        except Exception as e:
            raise ParseError(f"Google Drive API error: {e}") from e

        if mime_type not in _EXPORT_MAP:
            raise UnsupportedFormatError(
                mime_type,
                "Only Google Docs, Sheets, and Slides are supported"
            )

        export_mime, export_ext = _EXPORT_MAP[mime_type]

        # Download exported file
        try:
            request  = service.files().export_media(fileId=file_id, mimeType=export_mime)
            buf      = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            content = buf.getvalue()
        except Exception as e:
            raise ParseError(f"Failed to export Google file {file_id!r}: {e}") from e

        # Delegate to the appropriate format parser
        from distill.registry import registry as _registry
        parser_cls = _registry.find(f"document{export_ext}")
        parser     = parser_cls()
        document   = parser.parse(content, options)

        # Enrich metadata with Google-specific info
        document.metadata.title         = document.metadata.title or file_name
        document.metadata.source_format = mime_type.split(".")[-1]  # e.g. "document"
        document.metadata.source_path   = str(source)

        return document

    def _extract_file_id(self, url: str) -> Optional[str]:
        """Extract the file ID from a Google Drive URL."""
        match = _FILE_ID_RE.search(url)
        return match.group(1) if match else None

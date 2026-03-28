"""
distill.parsers.google
~~~~~~~~~~~~~~~~~~~~~~
Parsers for Google Workspace documents via the Google Drive Export API.

Each parser fetches the document from Drive, exports it to the corresponding
Office format, then delegates to the native Distill parser for content
extraction:

    GoogleDocsParser   → exports to .docx → DocxParser
    GoogleSheetsParser → exports to .xlsx → XlsxParser
    GoogleSlidesParser → exports to .pptx → PptxParser

Input
-----
``source`` may be:
  - A Google Drive share / edit URL:
    ``https://docs.google.com/document/d/<FILE_ID>/edit``
  - A bare file ID string (28+ alphanumeric/dash/underscore characters)
  - A local ``.gdoc`` / ``.gsheet`` / ``.gslides`` shortcut path (the file
    stem is treated as the file ID)

Authentication
--------------
Credentials are resolved in this order:

1. ``options.extra['google_credentials']`` — one of:
     - A ``google.oauth2.credentials.Credentials`` or
       ``google.oauth2.service_account.Credentials`` object (already built)
     - A path string to a service account JSON key file
2. ``options.extra['access_token']`` — a raw OAuth2 access token string
3. ``DISTILL_GOOGLE_CREDENTIALS`` environment variable — path to a service
   account JSON key file

Install
-------
    pip install distill-core[google]
    # adds: google-api-python-client, google-auth
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Union

from distill.ir import Document, DocumentMetadata
from distill.parsers.base import ParseError, ParseOptions, Parser, UnsupportedFormatError
from distill.registry import registry


# ── Constants ─────────────────────────────────────────────────────────────────

# Maps Google MIME type → (export MIME type, file extension, source_format label)
_EXPORT_MAP: dict[str, tuple[str, str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
        "google-docs",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
        "google-sheets",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
        "google-slides",
    ),
}

# Regex to pull a file ID from a Drive URL's /d/<ID> segment.
# Google file IDs are 28–44 characters of [A-Za-z0-9_-].
_FILE_ID_FROM_URL_RE = re.compile(r"/d/([A-Za-z0-9_-]{25,})")

# Regex to recognise a bare file ID (no URL scaffolding).
# Drive IDs are 25–50 alphanumeric / dash / underscore chars.
_BARE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{25,50}$")

# Required packages (checked at registration time)
_REQUIRES = ["googleapiclient", "google.auth"]

# Google Drive API scopes needed for read-only export
_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


# ── Shared helpers ────────────────────────────────────────────────────────────

def _extract_file_id(source: str) -> Optional[str]:
    """
    Return the Google Drive file ID embedded in *source*, or ``None``.

    Handles:
    - Full Drive edit / share URLs  (extracts from ``/d/<id>/``)
    - Bare file ID strings
    - Local ``.gdoc`` / ``.gsheet`` / ``.gslides`` shortcut paths
      (the filename stem is used as the file ID)
    """
    # Strip leading/trailing whitespace
    s = str(source).strip()

    # 1. URL with /d/<id>/ pattern
    m = _FILE_ID_FROM_URL_RE.search(s)
    if m:
        return m.group(1)

    # 2. Bare file ID
    if _BARE_FILE_ID_RE.match(s):
        return s

    # 3. Local shortcut file — stem is the file ID
    p = Path(s)
    if p.suffix.lower() in {".gdoc", ".gsheet", ".gslides"}:
        stem = p.stem
        if _BARE_FILE_ID_RE.match(stem):
            return stem

    return None


def _build_credentials(options: ParseOptions):
    """
    Build and return a Google credentials object from *options*.

    Resolution order:
      1. ``options.extra['google_credentials']`` (object or path string)
      2. ``options.extra['access_token']`` (raw token string)
      3. ``DISTILL_GOOGLE_CREDENTIALS`` env var (path to service account JSON)

    Raises ``ParseError`` if no credentials can be found.
    """
    try:
        from google.oauth2 import credentials as ga_credentials
        from google.oauth2 import service_account
    except ImportError as e:
        raise ParseError(
            f"Google auth library not available: {e}. "
            "Install with: pip install distill-core[google]"
        ) from e

    # Option 1: credentials object or path in extra
    creds_hint = options.extra.get("google_credentials")
    if creds_hint is not None:
        if hasattr(creds_hint, "token") or hasattr(creds_hint, "service_account_email"):
            # Already a Credentials object
            return creds_hint
        # Treat as a path to a service account JSON file
        try:
            return service_account.Credentials.from_service_account_file(
                str(creds_hint), scopes=_SCOPES
            )
        except Exception as e:
            raise ParseError(
                f"Could not load Google service account credentials from "
                f"{creds_hint!r}: {e}"
            ) from e

    # Option 2: raw access token in extra
    token = options.extra.get("access_token")
    if token:
        return ga_credentials.Credentials(token=token)

    # Option 3: env var pointing at service account JSON
    env_path = os.environ.get("DISTILL_GOOGLE_CREDENTIALS")
    if env_path:
        try:
            return service_account.Credentials.from_service_account_file(
                env_path, scopes=_SCOPES
            )
        except Exception as e:
            raise ParseError(
                f"Could not load Google credentials from DISTILL_GOOGLE_CREDENTIALS "
                f"({env_path!r}): {e}"
            ) from e

    raise ParseError(
        "Google Workspace parsing requires credentials.  Provide one of:\n"
        "  • options.extra['google_credentials'] = '/path/to/service-account.json'\n"
        "  • options.extra['access_token']       = 'ya29...'\n"
        "  • DISTILL_GOOGLE_CREDENTIALS          = '/path/to/service-account.json'"
    )


def _export_via_drive(
    file_id: str,
    expected_google_mime: str,
    credentials,
    source_label: str,
) -> tuple[bytes, str]:
    """
    Fetch *file_id* from Google Drive and export it.

    Parameters
    ----------
    file_id:
        Google Drive file ID.
    expected_google_mime:
        The Google MIME type we expect this file to be (used to derive the
        export format).  If the actual MIME type doesn't match, an
        ``UnsupportedFormatError`` is raised.
    credentials:
        A Google credentials object.
    source_label:
        Human-readable label for error messages (e.g. the original URL).

    Returns
    -------
    (content_bytes, file_name)
        The exported file bytes and the original file name from Drive.

    Raises
    ------
    ParseError
        On any Drive API error or unsupported MIME type.
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError as e:
        raise ParseError(
            f"Google API client not available: {e}. "
            "Install with: pip install distill-core[google]"
        ) from e

    try:
        service = build("drive", "v3", credentials=credentials)
    except Exception as e:
        raise ParseError(f"Failed to initialise Google Drive service: {e}") from e

    # Fetch file metadata
    try:
        meta = service.files().get(
            fileId=file_id, fields="mimeType,name"
        ).execute()
    except Exception as e:
        _raise_drive_error(e, file_id, source_label)

    actual_mime = meta.get("mimeType", "")
    file_name   = meta.get("name", "document")

    if actual_mime != expected_google_mime:
        raise UnsupportedFormatError(
            actual_mime,
            f"Expected a Google {expected_google_mime.split('.')[-1].title()} "
            f"but got {actual_mime!r}.  Use the matching Distill parser."
        )

    export_mime, _, _ = _EXPORT_MAP[expected_google_mime]

    # Export the file
    try:
        request = service.files().export_media(
            fileId=file_id, mimeType=export_mime
        )
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue(), file_name
    except Exception as e:
        _raise_drive_error(e, file_id, source_label)


def _raise_drive_error(exc: Exception, file_id: str, source_label: str) -> None:
    """Translate a googleapiclient HttpError into a descriptive ParseError."""
    msg = str(exc)

    # googleapiclient wraps HTTP errors — try to extract status code
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == "403" or "403" in msg:
        raise ParseError(
            f"Permission denied accessing Google Drive file {file_id!r}. "
            "Check that the credentials have read access to the file."
        ) from exc
    if status == "404" or "404" in msg:
        raise ParseError(
            f"Google Drive file {file_id!r} not found. "
            "Verify the file ID or URL and that the file has not been deleted."
        ) from exc

    raise ParseError(
        f"Google Drive API error for {source_label!r}: {msg}"
    ) from exc


def _parse_exported(
    content: bytes,
    ext: str,
    file_name: str,
    source_format_label: str,
    source_path: str,
    options: ParseOptions,
) -> Document:
    """
    Parse exported bytes using the appropriate Distill parser, then
    annotate metadata with Google-specific information.
    """
    # Import the delegate parser lazily to avoid circular imports
    from distill.registry import registry as _reg

    # registry.find() works by extension — synthesise a fake filename
    parser_cls = _reg.find(f"document{ext}")
    document = parser_cls().parse(content, options)

    # Override metadata with Google-specific values
    if file_name:
        document.metadata.title = document.metadata.title or file_name
    document.metadata.source_format = source_format_label
    document.metadata.source_path   = source_path

    return document


# ── Parser classes ────────────────────────────────────────────────────────────

@registry.register
class GoogleDocsParser(Parser):
    """
    Converts a Google Docs document to Markdown via Drive API export (.docx).

    Accepts a Drive URL, bare file ID, or local ``.gdoc`` shortcut path.
    Delegates to ``DocxParser`` after export.
    """

    extensions = [".gdoc"]
    mime_types = ["application/vnd.google-apps.document"]
    requires   = _REQUIRES

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        if isinstance(source, bytes):
            raise ParseError(
                "GoogleDocsParser does not accept raw bytes. "
                "Pass a Google Drive URL or file ID string."
            )

        options   = options or ParseOptions()
        source_s  = str(source)
        file_id   = _extract_file_id(source_s)

        if not file_id:
            raise ParseError(
                f"Could not extract a Google Drive file ID from: {source_s!r}. "
                "Pass a Drive URL (https://docs.google.com/document/d/<ID>/...) "
                "or a bare file ID string."
            )

        creds = _build_credentials(options)
        content, file_name = _export_via_drive(
            file_id,
            "application/vnd.google-apps.document",
            creds,
            source_s,
        )

        _, ext, fmt_label = _EXPORT_MAP["application/vnd.google-apps.document"]
        return _parse_exported(content, ext, file_name, fmt_label, source_s, options)


@registry.register
class GoogleSheetsParser(Parser):
    """
    Converts a Google Sheets spreadsheet to Markdown via Drive API export (.xlsx).

    Accepts a Drive URL, bare file ID, or local ``.gsheet`` shortcut path.
    Delegates to ``XlsxParser`` after export.
    """

    extensions = [".gsheet"]
    mime_types = ["application/vnd.google-apps.spreadsheet"]
    requires   = _REQUIRES

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        if isinstance(source, bytes):
            raise ParseError(
                "GoogleSheetsParser does not accept raw bytes. "
                "Pass a Google Drive URL or file ID string."
            )

        options   = options or ParseOptions()
        source_s  = str(source)
        file_id   = _extract_file_id(source_s)

        if not file_id:
            raise ParseError(
                f"Could not extract a Google Drive file ID from: {source_s!r}. "
                "Pass a Drive URL (https://docs.google.com/spreadsheets/d/<ID>/...) "
                "or a bare file ID string."
            )

        creds = _build_credentials(options)
        content, file_name = _export_via_drive(
            file_id,
            "application/vnd.google-apps.spreadsheet",
            creds,
            source_s,
        )

        _, ext, fmt_label = _EXPORT_MAP["application/vnd.google-apps.spreadsheet"]
        return _parse_exported(content, ext, file_name, fmt_label, source_s, options)


@registry.register
class GoogleSlidesParser(Parser):
    """
    Converts a Google Slides presentation to Markdown via Drive API export (.pptx).

    Accepts a Drive URL, bare file ID, or local ``.gslides`` shortcut path.
    Delegates to ``PptxParser`` after export.
    """

    extensions = [".gslides"]
    mime_types = ["application/vnd.google-apps.presentation"]
    requires   = _REQUIRES

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        if isinstance(source, bytes):
            raise ParseError(
                "GoogleSlidesParser does not accept raw bytes. "
                "Pass a Google Drive URL or file ID string."
            )

        options   = options or ParseOptions()
        source_s  = str(source)
        file_id   = _extract_file_id(source_s)

        if not file_id:
            raise ParseError(
                f"Could not extract a Google Drive file ID from: {source_s!r}. "
                "Pass a Drive URL (https://docs.google.com/presentation/d/<ID>/...) "
                "or a bare file ID string."
            )

        creds = _build_credentials(options)
        content, file_name = _export_via_drive(
            file_id,
            "application/vnd.google-apps.presentation",
            creds,
            source_s,
        )

        _, ext, fmt_label = _EXPORT_MAP["application/vnd.google-apps.presentation"]
        return _parse_exported(content, ext, file_name, fmt_label, source_s, options)

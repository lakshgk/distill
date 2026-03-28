"""
distill.parsers._libreoffice
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Shared LibreOffice headless conversion utility.

Used by DocLegacyParser, XlsLegacyParser, and PptLegacyParser to convert
legacy binary formats (.doc, .xls, .ppt) to their modern OOXML equivalents
before delegating to the corresponding native parser.

Usage:

    from distill.parsers._libreoffice import convert_via_libreoffice

    output_path = convert_via_libreoffice(
        source     = "/path/to/file.doc",
        target_ext = "docx",
        timeout    = 30,
    )
    try:
        doc = DocxParser().parse(output_path)
    finally:
        output_path.unlink(missing_ok=True)
        output_path.parent.rmdir()

The caller is responsible for cleaning up the returned temp directory.

LibreOffice detection
---------------------
Tries the following executables in order:
  1. ``libreoffice``
  2. ``soffice``
  3. ``/usr/lib/libreoffice/program/soffice``
  4. ``/Applications/LibreOffice.app/Contents/MacOS/soffice``  (macOS)
  5. ``C:/Program Files/LibreOffice/program/soffice.exe``      (Windows)

The search order and additional paths can be overridden by setting the
``DISTILL_LIBREOFFICE`` environment variable to the full path of the binary.

Timeout
-------
Default timeout is 60 seconds.  Override via the ``timeout`` argument or
``options.extra['libreoffice_timeout']``.

Concurrent calls
----------------
Each call creates its own isolated temp directory (used as both the output
directory and as ``UserInstallation``) so multiple conversions can run in
parallel without lock conflicts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union

from distill.parsers.base import ParseError


# ── Binary discovery ──────────────────────────────────────────────────────────

_CANDIDATE_PATHS = [
    "libreoffice",
    "soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/usr/lib/libreoffice/program/soffice.bin",
    "/opt/libreoffice/program/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def find_libreoffice() -> Optional[str]:
    """
    Return the path to the LibreOffice / soffice executable, or None if not found.

    Checks the ``DISTILL_LIBREOFFICE`` environment variable first, then
    falls back to the standard candidate paths.
    """
    env_override = os.environ.get("DISTILL_LIBREOFFICE")
    if env_override:
        p = Path(env_override)
        if p.is_file():
            return str(p)

    for candidate in _CANDIDATE_PATHS:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        # Also try as a direct path (handles absolute paths not on PATH)
        p = Path(candidate)
        if p.is_file():
            return str(p)

    return None


def is_libreoffice_available() -> bool:
    """Return True if a usable LibreOffice binary can be found."""
    return find_libreoffice() is not None


# ── Conversion ────────────────────────────────────────────────────────────────

def convert_via_libreoffice(
    source:     Union[str, Path, bytes],
    target_ext: str,
    timeout:    int = 60,
) -> Path:
    """
    Convert a document to a modern OOXML format using LibreOffice headless.

    Parameters
    ----------
    source:
        File path, Path object, or raw bytes of the source document.
    target_ext:
        Target file extension without the leading dot, e.g. ``"docx"``,
        ``"xlsx"``, ``"pptx"``.
    timeout:
        Maximum seconds to wait for LibreOffice to finish. Default: 60.

    Returns
    -------
    Path
        Path to the converted file inside a newly created temp directory.
        The caller **must** clean up the temp directory when done:

            output_path.parent.rmdir()  # or shutil.rmtree(output_path.parent)

    Raises
    ------
    ParseError
        - LibreOffice is not installed / not on PATH
        - Conversion process times out
        - Conversion process exits with a non-zero code
        - Expected output file is not produced
    """
    lo_bin = find_libreoffice()
    if not lo_bin:
        raise ParseError(
            "LibreOffice is not installed or not on PATH. "
            "Install LibreOffice (https://www.libreoffice.org/download) "
            "and ensure 'libreoffice' or 'soffice' is available on PATH, "
            "or set the DISTILL_LIBREOFFICE environment variable to the full path. "
            "Alternatively, use the Distill Docker image which bundles LibreOffice."
        )

    # Create an isolated temp directory for this conversion.
    # Using it as UserInstallation avoids lock conflicts with concurrent calls.
    tmp_dir = Path(tempfile.mkdtemp(prefix="distill_lo_"))

    try:
        # Write bytes input to disk if necessary
        if isinstance(source, bytes):
            src_path = tmp_dir / f"input.{target_ext.replace('x', '')}"
            src_path.write_bytes(source)
        else:
            src_path = Path(source)

        cmd = [
            lo_bin,
            f"-env:UserInstallation=file://{tmp_dir}",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to", target_ext,
            "--outdir", str(tmp_dir),
            str(src_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise ParseError(
                f"LibreOffice conversion timed out after {timeout}s. "
                f"Increase the limit via options.extra['libreoffice_timeout']."
            )
        except FileNotFoundError:
            raise ParseError(
                f"LibreOffice binary not found at: {lo_bin!r}. "
                "Set DISTILL_LIBREOFFICE to the correct path."
            )

        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise ParseError(
                f"LibreOffice conversion failed (exit {result.returncode}). "
                f"Details: {stderr[:300]}" if stderr else
                f"LibreOffice conversion failed (exit {result.returncode})."
            )

        # Locate the output file: same stem as input, with new extension
        stem       = src_path.stem
        output     = tmp_dir / f"{stem}.{target_ext}"

        if not output.exists():
            # LibreOffice sometimes produces a different stem — find any matching ext
            candidates = list(tmp_dir.glob(f"*.{target_ext}"))
            # Exclude the source file itself if it happens to have the same ext
            candidates = [c for c in candidates if c != src_path]
            if not candidates:
                raise ParseError(
                    f"LibreOffice ran successfully but produced no .{target_ext} file. "
                    f"Files in output dir: {[f.name for f in tmp_dir.iterdir()]}"
                )
            output = candidates[0]

        return output

    except ParseError:
        # Clean up on error — caller won't get the path so we must tidy up
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise ParseError(f"Unexpected error during LibreOffice conversion: {e}") from e

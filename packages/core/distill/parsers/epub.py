"""
distill.parsers.epub
~~~~~~~~~~~~~~~~~~~~
Parser for EPUB documents (.epub).

EPUB files are ZIP archives containing XHTML content files, an OPF package
descriptor (metadata + spine order), and a container.xml that locates the OPF.

This parser reads the spine in order, delegates each XHTML item to HTMLParser,
and merges the results into a single IR Document.

Install:
    pip install distill-core        # defusedxml is a core dependency
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional, Union

import defusedxml.ElementTree as ET

from distill.ir import Document, DocumentMetadata, Section
from distill.parsers.base import ParseOptions, Parser
from distill.registry import registry

log = logging.getLogger(__name__)

# OPF / Dublin Core XML namespaces
_NS_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
_NS_OPF = "http://www.idpf.org/2007/opf"
_NS_DC = "http://purl.org/dc/elements/1.1/"


def _parse_opf(opf_bytes: bytes) -> dict:
    """
    Extract metadata and spine order from an OPF package document.

    Returns a dict with keys:
        title    (str | None)
        author   (str | None)
        language (str | None)
        spine    (list[str])  — manifest hrefs in spine order
    """
    result: dict = {
        "title": None,
        "author": None,
        "language": None,
        "spine": [],
    }

    try:
        root = ET.fromstring(opf_bytes)
    except Exception:
        log.warning("Failed to parse OPF XML")
        return result

    # ── Metadata ────────────────────────────────────────────────────────
    metadata_el = root.find(f"{{{_NS_OPF}}}metadata")
    if metadata_el is None:
        # Try without namespace (some EPUBs omit the OPF namespace)
        metadata_el = root.find("metadata")

    if metadata_el is not None:
        title_el = metadata_el.find(f"{{{_NS_DC}}}title")
        if title_el is not None and title_el.text:
            result["title"] = title_el.text.strip()

        creator_el = metadata_el.find(f"{{{_NS_DC}}}creator")
        if creator_el is not None and creator_el.text:
            result["author"] = creator_el.text.strip()

        lang_el = metadata_el.find(f"{{{_NS_DC}}}language")
        if lang_el is not None and lang_el.text:
            result["language"] = lang_el.text.strip()

    # ── Manifest (id → href mapping) ────────────────────────────────────
    manifest_el = root.find(f"{{{_NS_OPF}}}manifest")
    if manifest_el is None:
        manifest_el = root.find("manifest")

    id_to_href: dict[str, str] = {}
    if manifest_el is not None:
        for item in manifest_el:
            tag = item.tag.split("}")[-1] if "}" in item.tag else item.tag
            if tag == "item":
                item_id = item.get("id") or ""
                item_href = item.get("href") or ""
                if item_id and item_href:
                    id_to_href[item_id] = item_href

    # ── Spine (ordered list of itemrefs) ────────────────────────────────
    spine_el = root.find(f"{{{_NS_OPF}}}spine")
    if spine_el is None:
        spine_el = root.find("spine")

    if spine_el is not None:
        for itemref in spine_el:
            tag = itemref.tag.split("}")[-1] if "}" in itemref.tag else itemref.tag
            if tag == "itemref":
                idref = itemref.get("idref") or ""
                href = id_to_href.get(idref)
                if href:
                    result["spine"].append(href)

    return result


@registry.register
class EPUBParser(Parser):
    """
    Parses .epub files into an IR Document.

    Opens the EPUB ZIP, reads the OPF package descriptor for metadata and
    spine order, then delegates each XHTML content document to HTMLParser
    and merges the results.
    """

    extensions = [".epub"]
    mime_types = ["application/epub+zip"]
    requires = ["defusedxml"]

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()

        # ── Open ZIP ────────────────────────────────────────────────────
        try:
            if isinstance(source, bytes):
                zf = zipfile.ZipFile(io.BytesIO(source))
            elif isinstance(source, (str, Path)):
                zf = zipfile.ZipFile(Path(source))
            else:
                zf = zipfile.ZipFile(io.BytesIO(bytes(source)))
        except (zipfile.BadZipFile, OSError, Exception) as exc:
            log.warning("Invalid or unreadable EPUB archive: %s", exc)
            return Document(
                metadata=DocumentMetadata(source_format="epub"),
            )

        with zf:
            # ── Locate OPF via container.xml ────────────────────────────
            opf_path = self._find_opf_path(zf)
            if opf_path is None:
                log.warning("No OPF package file found in EPUB")
                return Document(
                    metadata=DocumentMetadata(source_format="epub"),
                )

            # ── Parse OPF for metadata + spine ──────────────────────────
            try:
                opf_bytes = zf.read(opf_path)
            except (KeyError, OSError) as exc:
                log.warning("Failed to read OPF file %s: %s", opf_path, exc)
                return Document(
                    metadata=DocumentMetadata(source_format="epub"),
                )

            opf = _parse_opf(opf_bytes)

            # Resolve spine hrefs relative to the OPF directory
            opf_dir = str(PurePosixPath(opf_path).parent)
            spine_paths: list[str] = []
            for href in opf.get("spine") or []:
                if opf_dir and opf_dir != ".":
                    full = f"{opf_dir}/{href}"
                else:
                    full = href
                spine_paths.append(full)

            # ── Parse each spine item via HTMLParser ─────────────────────
            from distill.parsers.html import HTMLParser

            html_parser = HTMLParser()
            all_sections: list[Section] = []
            total_word_count = 0

            for item_path in spine_paths:
                try:
                    item_bytes = zf.read(item_path)
                except (KeyError, OSError) as exc:
                    log.warning(
                        "Skipping spine item %s: %s", item_path, exc
                    )
                    continue

                try:
                    # Strip XML declaration and XHTML namespace so HTMLParser
                    # handles tags correctly
                    import re as _re
                    item_text = item_bytes.decode("utf-8", errors="replace")
                    item_text = _re.sub(r"<\?xml[^?]*\?>", "", item_text)
                    item_text = _re.sub(
                        r"""\s+xmlns\s*=\s*["'][^"']*["']""", "", item_text
                    )
                    item_doc = html_parser.parse(item_text.encode("utf-8"), options)
                except Exception as exc:
                    log.warning(
                        "Failed to parse spine item %s: %s", item_path, exc
                    )
                    continue

                if item_doc is not None:
                    all_sections.extend(item_doc.sections or [])
                    item_wc = (
                        (item_doc.metadata.word_count or 0)
                        if item_doc.metadata is not None
                        else 0
                    )
                    total_word_count += item_wc

        # ── Build final Document ────────────────────────────────────────
        metadata = DocumentMetadata(
            title=opf.get("title"),
            author=opf.get("author"),
            language=opf.get("language"),
            source_format="epub",
            word_count=total_word_count if total_word_count > 0 else None,
        )

        return Document(
            metadata=metadata,
            sections=all_sections,
        )

    # ── Private helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _find_opf_path(zf: zipfile.ZipFile) -> Optional[str]:
        """
        Locate the OPF package file inside the EPUB ZIP.

        Primary: read META-INF/container.xml and extract the rootfile path.
        Fallback: scan the ZIP for any .opf file.
        """
        # Primary: container.xml
        try:
            container_bytes = zf.read("META-INF/container.xml")
            root = ET.fromstring(container_bytes)

            # Search with namespace
            for rootfile in root.iter(f"{{{_NS_CONTAINER}}}rootfile"):
                full_path = rootfile.get("full-path")
                if full_path:
                    return full_path

            # Search without namespace (some EPUBs)
            for rootfile in root.iter("rootfile"):
                full_path = rootfile.get("full-path")
                if full_path:
                    return full_path
        except (KeyError, OSError, Exception) as exc:
            log.debug("container.xml not found or unreadable: %s", exc)

        # Fallback: find any .opf file in the archive
        for name in zf.namelist():
            if name.lower().endswith(".opf"):
                return name

        return None

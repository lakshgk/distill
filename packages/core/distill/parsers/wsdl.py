"""
distill.parsers.wsdl
~~~~~~~~~~~~~~~~~~~~
Parser for WSDL documents (.wsdl, .wsd).

Handles both WSDL 1.1 (namespace http://schemas.xmlsoap.org/wsdl/)
and WSDL 2.0 (namespace http://www.w3.org/ns/wsdl). Detects the
version from the root element namespace.

All XML parsing uses defusedxml — never stdlib xml.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

import defusedxml.ElementTree as ET

from distill.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Section,
    Table,
    TableCell,
    TableRow,
    TextRun,
)
from distill.parsers.base import ParseOptions, Parser
from distill.registry import registry
from distill.warnings import ConversionWarning, WarningType

log = logging.getLogger(__name__)

# ── Namespace constants ──────────────────────────────────────────────────────

NS_WSDL11 = "http://schemas.xmlsoap.org/wsdl/"
NS_WSDL20 = "http://www.w3.org/ns/wsdl"
NS_XSD = "http://www.w3.org/2001/XMLSchema"

# Shorthand for ElementTree tag matching
_NS11 = f"{{{NS_WSDL11}}}"
_NS20 = f"{{{NS_WSDL20}}}"
_NSXSD = f"{{{NS_XSD}}}"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _local(tag: Optional[str]) -> str:
    """Strip namespace from an element tag, returning the local name."""
    if not tag:
        return ""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _attr(elem, name: str) -> str:
    """Get an attribute value, returning empty string if absent."""
    return (elem.get(name) or "").strip()


def _doc_text(elem, ns_prefix: str) -> Optional[str]:
    """Extract text from a <wsdl:documentation> child, if present."""
    doc_elem = elem.find(f"{ns_prefix}documentation")
    if doc_elem is not None:
        text = "".join(doc_elem.itertext()).strip()
        return text if text else None
    return None


def _type_name(type_str: str) -> str:
    """Simplify a QName like 'xsd:string' or 'tns:Foo' to the local part."""
    if ":" in type_str:
        return type_str.split(":", 1)[1]
    return type_str


def _word_count(text: str) -> int:
    """Count words by whitespace splitting."""
    return len(text.split())


# ── XSD type parsing ────────────────────────────────────────────────────────

def _parse_xsd_types(root, ns_prefix: str) -> list[Section]:
    """Parse <wsdl:types> containing XSD schema(s) into sections."""
    sections: list[Section] = []
    types_elem = root.find(f"{ns_prefix}types")
    if types_elem is None:
        return sections

    for schema in types_elem:
        if _local(schema.tag) != "schema":
            continue
        for child in schema:
            local = _local(child.tag)
            name = _attr(child, "name")
            if not name:
                continue

            if local in ("element", "complexType", "simpleType"):
                sec = Section(
                    level=2,
                    heading=[TextRun(text=f"Type: {name}")],
                    blocks=[],
                )

                # Documentation
                doc = _doc_text(child, _NSXSD)
                if doc:
                    sec.blocks.append(Paragraph(runs=[TextRun(text=doc)]))

                # Collect fields from complexType > sequence/all
                fields = _collect_xsd_fields(child)
                if fields:
                    header = TableRow(cells=[
                        TableCell(content=[TextRun(text="Field")], is_header=True),
                        TableCell(content=[TextRun(text="Type")], is_header=True),
                    ])
                    rows = [header]
                    for fname, ftype in fields:
                        rows.append(TableRow(cells=[
                            TableCell(content=[TextRun(text=fname)]),
                            TableCell(content=[TextRun(text=ftype)]),
                        ]))
                    sec.blocks.append(Table(rows=rows))

                sections.append(sec)

    return sections


def _collect_xsd_fields(elem) -> list[tuple[str, str]]:
    """Recursively find <xsd:element> children inside complexType/sequence/all."""
    fields: list[tuple[str, str]] = []
    for child in elem:
        local = _local(child.tag)
        if local == "element":
            name = _attr(child, "name")
            typ = _attr(child, "type")
            if name:
                fields.append((name, _type_name(typ) if typ else "—"))
        elif local in ("complexType", "sequence", "all", "choice", "complexContent",
                        "extension", "restriction"):
            fields.extend(_collect_xsd_fields(child))
    return fields


# ── WSDL 1.1 parsing ────────────────────────────────────────────────────────

def _parse_messages_11(root) -> list[Table]:
    """Parse <wsdl:message> elements into Tables."""
    tables: list[Table] = []
    for msg in root.findall(f"{_NS11}message"):
        name = _attr(msg, "name")
        if not name:
            continue

        header = TableRow(cells=[
            TableCell(content=[TextRun(text="Part Name")], is_header=True),
            TableCell(content=[TextRun(text="Type")], is_header=True),
        ])
        rows = [header]
        for part in msg.findall(f"{_NS11}part"):
            pname = _attr(part, "name")
            ptype = _attr(part, "type") or _attr(part, "element")
            rows.append(TableRow(cells=[
                TableCell(content=[TextRun(text=pname or "—")]),
                TableCell(content=[TextRun(text=_type_name(ptype) if ptype else "—")]),
            ]))

        if len(rows) > 1:
            tables.append((name, Table(rows=rows)))
        else:
            tables.append((name, None))
    return tables


def _parse_port_types_11(root, message_tables: dict) -> list[Section]:
    """Parse <wsdl:portType> elements."""
    sections: list[Section] = []
    for pt in root.findall(f"{_NS11}portType"):
        name = _attr(pt, "name")
        if not name:
            continue

        sec = Section(
            level=2,
            heading=[TextRun(text=f"Port Type: {name}")],
            blocks=[],
        )

        doc = _doc_text(pt, _NS11)
        if doc:
            sec.blocks.append(Paragraph(runs=[TextRun(text=doc)]))

        for op in pt.findall(f"{_NS11}operation"):
            op_name = _attr(op, "name")
            if not op_name:
                continue

            op_sec = Section(
                level=3,
                heading=[TextRun(text=f"Operation: {op_name}")],
                blocks=[],
            )

            op_doc = _doc_text(op, _NS11)
            if op_doc:
                op_sec.blocks.append(Paragraph(runs=[TextRun(text=op_doc)]))

            # Input
            inp = op.find(f"{_NS11}input")
            if inp is not None:
                msg_ref = _type_name(_attr(inp, "message")) if _attr(inp, "message") else _attr(inp, "name") or "—"
                op_sec.blocks.append(Paragraph(runs=[TextRun(text=f"Input: {msg_ref}")]))
                tbl = message_tables.get(msg_ref)
                if tbl is not None:
                    op_sec.blocks.append(tbl)

            # Output
            out = op.find(f"{_NS11}output")
            if out is not None:
                msg_ref = _type_name(_attr(out, "message")) if _attr(out, "message") else _attr(out, "name") or "—"
                op_sec.blocks.append(Paragraph(runs=[TextRun(text=f"Output: {msg_ref}")]))
                tbl = message_tables.get(msg_ref)
                if tbl is not None:
                    op_sec.blocks.append(tbl)

            # Fault
            for fault in op.findall(f"{_NS11}fault"):
                msg_ref = _type_name(_attr(fault, "message")) if _attr(fault, "message") else _attr(fault, "name") or "—"
                op_sec.blocks.append(Paragraph(runs=[TextRun(text=f"Fault: {msg_ref}")]))

            sec.subsections.append(op_sec)

        sections.append(sec)
    return sections


def _parse_bindings_11(root) -> list[Section]:
    """Parse <wsdl:binding> elements."""
    sections: list[Section] = []
    for binding in root.findall(f"{_NS11}binding"):
        name = _attr(binding, "name")
        btype = _attr(binding, "type")
        if not name:
            continue

        heading_text = f"Binding: {name}"
        if btype:
            heading_text += f" (type: {_type_name(btype)})"

        sec = Section(
            level=2,
            heading=[TextRun(text=heading_text)],
            blocks=[],
        )

        doc = _doc_text(binding, _NS11)
        if doc:
            sec.blocks.append(Paragraph(runs=[TextRun(text=doc)]))

        for op in binding.findall(f"{_NS11}operation"):
            op_name = _attr(op, "name")
            if op_name:
                sec.blocks.append(Paragraph(runs=[TextRun(text=f"Operation: {op_name}")]))

        sections.append(sec)
    return sections


def _parse_services_11(root) -> list[Section]:
    """Parse <wsdl:service> elements as level-1 sections."""
    sections: list[Section] = []
    for svc in root.findall(f"{_NS11}service"):
        name = _attr(svc, "name")
        if not name:
            continue

        sec = Section(
            level=1,
            heading=[TextRun(text=f"Service: {name}")],
            blocks=[],
        )

        doc = _doc_text(svc, _NS11)
        if doc:
            sec.blocks.append(Paragraph(runs=[TextRun(text=doc)]))

        for port in svc.findall(f"{_NS11}port"):
            port_name = _attr(port, "name")
            port_binding = _attr(port, "binding")
            parts = []
            if port_name:
                parts.append(f"Port: {port_name}")
            if port_binding:
                parts.append(f"Binding: {_type_name(port_binding)}")
            if parts:
                sec.blocks.append(Paragraph(runs=[TextRun(text=" — ".join(parts))]))

        sections.append(sec)
    return sections


# ── WSDL 2.0 parsing ────────────────────────────────────────────────────────

def _parse_interfaces_20(root) -> list[Section]:
    """Parse <wsdl:interface> elements (WSDL 2.0 equivalent of portType)."""
    sections: list[Section] = []
    for iface in root.findall(f"{_NS20}interface"):
        name = _attr(iface, "name")
        if not name:
            continue

        sec = Section(
            level=2,
            heading=[TextRun(text=f"Interface: {name}")],
            blocks=[],
        )

        doc = _doc_text(iface, _NS20)
        if doc:
            sec.blocks.append(Paragraph(runs=[TextRun(text=doc)]))

        for op in iface.findall(f"{_NS20}operation"):
            op_name = _attr(op, "name")
            if not op_name:
                continue

            op_sec = Section(
                level=3,
                heading=[TextRun(text=f"Operation: {op_name}")],
                blocks=[],
            )

            op_doc = _doc_text(op, _NS20)
            if op_doc:
                op_sec.blocks.append(Paragraph(runs=[TextRun(text=op_doc)]))

            inp = op.find(f"{_NS20}input")
            if inp is not None:
                elem_ref = _attr(inp, "element") or _attr(inp, "messageLabel") or "—"
                op_sec.blocks.append(Paragraph(runs=[TextRun(text=f"Input: {_type_name(elem_ref)}")]))

            out = op.find(f"{_NS20}output")
            if out is not None:
                elem_ref = _attr(out, "element") or _attr(out, "messageLabel") or "—"
                op_sec.blocks.append(Paragraph(runs=[TextRun(text=f"Output: {_type_name(elem_ref)}")]))

            sec.subsections.append(op_sec)

        sections.append(sec)
    return sections


def _parse_bindings_20(root) -> list[Section]:
    """Parse <wsdl:binding> elements for WSDL 2.0."""
    sections: list[Section] = []
    for binding in root.findall(f"{_NS20}binding"):
        name = _attr(binding, "name")
        btype = _attr(binding, "type") or _attr(binding, "interface")
        if not name:
            continue

        heading_text = f"Binding: {name}"
        if btype:
            heading_text += f" (interface: {_type_name(btype)})"

        sec = Section(
            level=2,
            heading=[TextRun(text=heading_text)],
            blocks=[],
        )

        doc = _doc_text(binding, _NS20)
        if doc:
            sec.blocks.append(Paragraph(runs=[TextRun(text=doc)]))

        for op in binding.findall(f"{_NS20}operation"):
            op_name = _attr(op, "ref") or _attr(op, "name")
            if op_name:
                sec.blocks.append(Paragraph(runs=[TextRun(text=f"Operation: {_type_name(op_name)}")]))

        sections.append(sec)
    return sections


def _parse_services_20(root) -> list[Section]:
    """Parse <wsdl:service> elements for WSDL 2.0."""
    sections: list[Section] = []
    for svc in root.findall(f"{_NS20}service"):
        name = _attr(svc, "name")
        iface = _attr(svc, "interface")
        if not name:
            continue

        heading_text = f"Service: {name}"
        if iface:
            heading_text += f" (interface: {_type_name(iface)})"

        sec = Section(
            level=1,
            heading=[TextRun(text=heading_text)],
            blocks=[],
        )

        doc = _doc_text(svc, _NS20)
        if doc:
            sec.blocks.append(Paragraph(runs=[TextRun(text=doc)]))

        for endpoint in svc.findall(f"{_NS20}endpoint"):
            ep_name = _attr(endpoint, "name")
            ep_binding = _attr(endpoint, "binding")
            parts = []
            if ep_name:
                parts.append(f"Endpoint: {ep_name}")
            if ep_binding:
                parts.append(f"Binding: {_type_name(ep_binding)}")
            if parts:
                sec.blocks.append(Paragraph(runs=[TextRun(text=" — ".join(parts))]))

        sections.append(sec)
    return sections


# ── Parser class ─────────────────────────────────────────────────────────────

@registry.register
class WSDLParser(Parser):
    """
    Parses WSDL 1.1 and 2.0 documents into an IR Document.

    Parse order: types -> messages -> portTypes/interfaces -> bindings -> services.
    """

    extensions = [".wsdl", ".wsd"]
    mime_types = ["application/wsdl+xml"]
    requires = ["defusedxml"]

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()
        collector = getattr(options, "collector", None)

        # Read source bytes/text
        if isinstance(source, bytes):
            raw = source.decode("utf-8", errors="replace")
        elif isinstance(source, (str, Path)):
            path = Path(source)
            if path.exists():
                raw = path.read_text(encoding="utf-8", errors="replace")
            else:
                raw = str(source)
        else:
            raw = str(source)

        # Parse XML with defusedxml
        try:
            root = ET.fromstring(raw)
        except Exception as exc:
            log.debug("WSDL XML parse failed: %s", exc)
            if collector is not None:
                try:
                    collector.add(ConversionWarning(
                        type=WarningType.CONTENT_EXTRACTED,
                        message="WSDL could not be fully parsed \u2014 output may be incomplete",
                    ))
                except Exception:
                    pass
            return Document(
                metadata=DocumentMetadata(source_format="wsdl", word_count=0),
                sections=[],
            )

        # Detect WSDL version from root namespace
        root_tag = root.tag or ""
        if NS_WSDL20 in root_tag:
            return self._parse_wsdl20(root, raw, collector)
        else:
            # Default to 1.1 (most common)
            return self._parse_wsdl11(root, raw, collector)

    def _parse_wsdl11(self, root, raw: str, collector) -> Document:
        """Build IR from a WSDL 1.1 document."""
        sections: list[Section] = []

        try:
            # 1. Types
            type_sections = _parse_xsd_types(root, _NS11)
            if type_sections:
                types_container = Section(
                    level=1,
                    heading=[TextRun(text="Types")],
                    blocks=[],
                    subsections=type_sections,
                )
                sections.append(types_container)

            # 2. Messages (parsed into lookup dict for portType references)
            msg_list = _parse_messages_11(root)
            message_tables: dict[str, Table] = {}
            if msg_list:
                msg_container = Section(
                    level=1,
                    heading=[TextRun(text="Messages")],
                    blocks=[],
                )
                for name, tbl in msg_list:
                    msg_sec = Section(
                        level=2,
                        heading=[TextRun(text=f"Message: {name}")],
                        blocks=[],
                    )
                    if tbl is not None:
                        msg_sec.blocks.append(tbl)
                        message_tables[name] = tbl
                    msg_container.subsections.append(msg_sec)
                sections.append(msg_container)

            # 3. Port Types
            pt_sections = _parse_port_types_11(root, message_tables)
            if pt_sections:
                pt_container = Section(
                    level=1,
                    heading=[TextRun(text="Port Types")],
                    blocks=[],
                    subsections=pt_sections,
                )
                sections.append(pt_container)

            # 4. Bindings
            binding_sections = _parse_bindings_11(root)
            if binding_sections:
                bind_container = Section(
                    level=1,
                    heading=[TextRun(text="Bindings")],
                    blocks=[],
                    subsections=binding_sections,
                )
                sections.append(bind_container)

            # 5. Services
            svc_sections = _parse_services_11(root)
            sections.extend(svc_sections)

            # Top-level documentation
            doc_text = _doc_text(root, _NS11)
            if doc_text:
                sections.insert(0, Section(
                    level=1,
                    heading=[TextRun(text="Documentation")],
                    blocks=[Paragraph(runs=[TextRun(text=doc_text)])],
                ))

        except Exception as exc:
            log.debug("WSDL 1.1 parse error: %s", exc)
            if collector is not None:
                try:
                    collector.add(ConversionWarning(
                        type=WarningType.CONTENT_EXTRACTED,
                        message="WSDL could not be fully parsed \u2014 output may be incomplete",
                    ))
                except Exception:
                    pass

        # Word count: sum of all visible text
        wc = self._count_words(raw)

        return Document(
            metadata=DocumentMetadata(source_format="wsdl", word_count=wc or None),
            sections=sections,
        )

    def _parse_wsdl20(self, root, raw: str, collector) -> Document:
        """Build IR from a WSDL 2.0 document."""
        sections: list[Section] = []

        try:
            # 1. Types
            type_sections = _parse_xsd_types(root, _NS20)
            if type_sections:
                types_container = Section(
                    level=1,
                    heading=[TextRun(text="Types")],
                    blocks=[],
                    subsections=type_sections,
                )
                sections.append(types_container)

            # 2. WSDL 2.0 has no <message> elements — skip

            # 3. Interfaces (equivalent to portType)
            iface_sections = _parse_interfaces_20(root)
            if iface_sections:
                iface_container = Section(
                    level=1,
                    heading=[TextRun(text="Interfaces")],
                    blocks=[],
                    subsections=iface_sections,
                )
                sections.append(iface_container)

            # 4. Bindings
            binding_sections = _parse_bindings_20(root)
            if binding_sections:
                bind_container = Section(
                    level=1,
                    heading=[TextRun(text="Bindings")],
                    blocks=[],
                    subsections=binding_sections,
                )
                sections.append(bind_container)

            # 5. Services
            svc_sections = _parse_services_20(root)
            sections.extend(svc_sections)

            # Top-level documentation
            doc_text = _doc_text(root, _NS20)
            if doc_text:
                sections.insert(0, Section(
                    level=1,
                    heading=[TextRun(text="Documentation")],
                    blocks=[Paragraph(runs=[TextRun(text=doc_text)])],
                ))

        except Exception as exc:
            log.debug("WSDL 2.0 parse error: %s", exc)
            if collector is not None:
                try:
                    collector.add(ConversionWarning(
                        type=WarningType.CONTENT_EXTRACTED,
                        message="WSDL could not be fully parsed \u2014 output may be incomplete",
                    ))
                except Exception:
                    pass

        wc = self._count_words(raw)

        return Document(
            metadata=DocumentMetadata(source_format="wsdl", word_count=wc or None),
            sections=sections,
        )

    @staticmethod
    def _count_words(raw: str) -> int:
        """Count words across all visible text (strip XML tags first)."""
        import re
        stripped = re.sub(r"<[^>]+>", " ", raw)
        return len(stripped.split())

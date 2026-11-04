"""Run this package against the shared `last-word/docx-constructs` table.

The rows are NOT transcribed here. This package, its PHP twin and its Node twin
all assert the same file, so a mapping that drifts in one engine fails in that
engine rather than quietly becoming that engine's behaviour. Adding a construct
means adding a row in `fancy-conformance`, once.

What lives here is only the extractors: how to get from `to_bytes(doc)` to the
value a row compares. They are deliberately thin -- a normaliser clever enough
to paper over a difference is a normaliser that stops the suite finding one.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

import fancy_conformance as conformance
import pytest

import last_word

SUITE = "last-word/docx-constructs"


def _document_xml(doc: dict[str, Any]) -> ET.Element:
    with zipfile.ZipFile(io.BytesIO(last_word.to_bytes(doc))) as archive:
        return ET.fromstring(archive.read("word/document.xml"))


def _local(tag: Any) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _norm_props(node: ET.Element | None) -> list:
    """An ordered normalisation of one property container.

    ORDER IS THE POINT: CT_RPr, CT_PPr, CT_TcPr, CT_TblPr and CT_SectPr are all
    xsd:sequence, so a map keyed by element name would let two engines emit
    different XML and still agree here. Attribute order is not pinned, because
    attributes are unordered in XML.
    """
    if node is None:
        return []
    out = []
    for child in list(node):
        if len(list(child)) > 0:
            out.append([_local(child.tag), _norm_props(child)])
            continue
        attrs = {_local(k): v for k, v in child.attrib.items()}
        out.append([_local(child.tag), attrs if attrs else True])
    return out


def _collect(root: ET.Element, name: str) -> list[ET.Element]:
    """Every element with this local name, in document order."""
    found: list[ET.Element] = []

    def walk(node: ET.Element) -> None:
        if _local(node.tag) == name:
            found.append(node)
        for child in list(node):
            walk(child)

    walk(root)
    return found


def _child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in list(parent):
        if _local(child.tag) == name:
            return child
    return None


def _run_props(doc: dict[str, Any]) -> list:
    out = []
    for r in _collect(_document_xml(doc), "r"):
        text = "".join(t.text or "" for t in _collect(r, "t"))
        out.append({"text": text, "rPr": _norm_props(_child(r, "rPr"))})
    return out


def _table_props(doc: dict[str, Any]) -> list:
    out = []
    for tbl in _collect(_document_xml(doc), "tbl"):
        grid = [g.attrib.get(f"{{{_W}}}w", "") for g in _collect(tbl, "gridCol")]
        out.append({"tblPr": _norm_props(_child(tbl, "tblPr")), "grid": grid})
    return out


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _written_parts(doc: dict[str, Any]) -> dict[str, bytes]:
    """Every part of a written document, by name."""
    with zipfile.ZipFile(io.BytesIO(last_word.to_bytes(doc))) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _part_root(xml: bytes | None) -> ET.Element | None:
    return None if xml is None else ET.fromstring(xml)


def _footnotes_part(doc: dict[str, Any]) -> dict[str, Any]:
    """The `footnotesPart` projection, as the manifest defines it."""
    parts = _written_parts(doc)

    content_type = None
    for override in _collect(ET.fromstring(parts["[Content_Types].xml"]), "Override"):
        if override.get("PartName") == "/word/footnotes.xml":
            content_type = override.get("ContentType")
    relationship = None
    for rel in _collect(ET.fromstring(parts["word/_rels/document.xml.rels"]), "Relationship"):
        if (rel.get("Type") or "").endswith("/footnotes"):
            relationship = rel.get("Target")
    settings = _part_root(parts.get("word/settings.xml"))
    footnote_pr = _child(settings, "footnotePr")

    notes = []
    root = _part_root(parts.get("word/footnotes.xml"))
    for node in list(root) if root is not None else []:
        if _local(node.tag) == "footnote":
            notes.append(
                {
                    "id": node.get(f"{{{_W}}}id", ""),
                    "type": node.get(f"{{{_W}}}type"),
                    "text": "".join(t.text or "" for t in _collect(node, "t")),
                    "content": _norm_props(node),
                }
            )

    return {
        "contentType": content_type,
        "relationship": relationship,
        "footnotePr": None if footnote_pr is None else _norm_props(footnote_pr),
        "references": [
            r.get(f"{{{_W}}}id", "")
            for r in _collect(ET.fromstring(parts["word/document.xml"]), "footnoteReference")
        ],
        "footnotes": notes,
    }

_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _body_sect_pr(parts: dict[str, bytes]) -> ET.Element | None:
    document = _part_root(parts.get("word/document.xml"))
    return _child(_child(document, "body"), "sectPr")


def _band_targets(parts: dict[str, bytes]) -> dict[str, str]:
    """Relationship id => target, for every header and footer relationship."""
    root = _part_root(parts.get("word/_rels/document.xml.rels"))
    out: dict[str, str] = {}
    for rel in list(root) if root is not None else []:
        if _local(rel.tag) == "Relationship" and re.search(r"/(header|footer)$", rel.get("Type") or ""):
            out[rel.get("Id") or ""] = rel.get("Target") or ""
    return out


def _band_references(parts: dict[str, bytes]) -> list[tuple[ET.Element, str | None]]:
    sect_pr = _body_sect_pr(parts)
    targets = _band_targets(parts)
    return [
        (node, targets.get(node.get(f"{{{_R}}}id") or ""))
        for node in (list(sect_pr) if sect_pr is not None else [])
        if _local(node.tag) in ("headerReference", "footerReference")
    ]


def _band_parts(doc: dict[str, Any]) -> dict[str, Any]:
    """The `bandParts` projection, as the manifest defines it."""
    parts = _written_parts(doc)
    sect_pr = _body_sect_pr(parts)

    references = []
    for node, target in _band_references(parts):
        part = _part_root(parts.get(f"word/{target}")) if target is not None else None
        references.append(
            {
                "kind": _local(node.tag)[: -len("Reference")],
                "type": node.get(f"{{{_W}}}type", ""),
                "target": target,
                "text": None if part is None else "".join(t.text or "" for t in _collect(part, "t")),
            }
        )
    settings = _part_root(parts.get("word/settings.xml"))

    return {
        "titlePg": sect_pr is not None and _child(sect_pr, "titlePg") is not None,
        "evenAndOddHeaders": settings is not None and _child(settings, "evenAndOddHeaders") is not None,
        "references": references,
        "parts": sorted(n for n in parts if re.match(r"^word/(header|footer)[^/]*\.xml$", n)),
    }


def _band_fields(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The `bandFields` projection: every complex field of each referenced part, once per target."""
    parts = _written_parts(doc)
    seen: set[str] = set()
    out = []
    for _node, target in _band_references(parts):
        if target is None or target in seen:
            continue
        seen.add(target)
        fields = []
        root = _part_root(parts.get(f"word/{target}"))
        state: str | None = None
        instruction = result = ""
        for el in root.iter() if root is not None else []:
            name = _local(el.tag)
            if name == "fldChar":
                kind = el.get(f"{{{_W}}}fldCharType")
                if kind == "begin":
                    state, instruction, result = "instruction", "", ""
                elif kind == "separate":
                    state = "result"
                elif kind == "end" and state is not None:
                    fields.append({"instruction": instruction, "result": result})
                    state = None
            elif name == "instrText" and state == "instruction":
                instruction += el.text or ""
            elif name == "t" and state == "result":
                result += el.text or ""
        out.append({"target": target, "fields": fields})
    return out


def _core_props(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The `coreProps` projection. Qualified names need the prefixes the part declares,
    which ElementTree drops, so they are collected from its start-ns events."""
    parts = _written_parts(doc)
    raw = parts.get("docProps/core.xml")
    if raw is None:
        return None
    prefixes: dict[str, str] = {}
    for _event, (prefix, uri) in ET.iterparse(io.BytesIO(raw), events=("start-ns",)):
        prefixes.setdefault(uri, prefix)
    core = ET.fromstring(raw)

    def qualified(tag: str) -> str:
        uri, _, local = tag[1:].partition("}") if tag.startswith("{") else ("", "", tag)
        prefix = prefixes.get(uri, "")
        return f"{prefix}:{local}" if prefix else local

    content_type = None
    for override in _collect(ET.fromstring(parts["[Content_Types].xml"]), "Override"):
        if override.get("PartName") == "/docProps/core.xml":
            content_type = override.get("ContentType")
    relationship = None
    for rel in _collect(ET.fromstring(parts["_rels/.rels"]), "Relationship"):
        if (rel.get("Type") or "").endswith("/core-properties"):
            relationship = rel.get("Target")

    return {
        "contentType": content_type,
        "relationship": relationship,
        "elements": [[qualified(el.tag), "".join(el.itertext())] for el in list(core)],
    }


EXTRACTORS = {
    "sections": lambda doc: _sections(doc),
    "runProps": _run_props,
    "paragraphProps": lambda doc: [
        _norm_props(_child(p, "pPr")) for p in _collect(_document_xml(doc), "p")
    ],
    "tableProps": _table_props,
    "cellProps": lambda doc: [
        _norm_props(_child(tc, "tcPr")) for tc in _collect(_document_xml(doc), "tc")
    ],
    "sectionProps": lambda doc: _norm_props(_child(_child(_document_xml(doc), "body"), "sectPr")),
    "footnotesPart": _footnotes_part,
    "bandParts": _band_parts,
    "bandFields": _band_fields,
    "coreProps": _core_props,
    "readBack": lambda doc: last_word.read(last_word.to_bytes(doc)),
    # The comparator is the suite's own, so "equal" means the same thing here
    # as it does for every other row.
    "roundTripFixpoint": lambda doc: {
        "fixpoint": conformance.equals(last_word.read(last_word.to_bytes(doc)), doc)
    },
}


class _NotApplicable(Exception):
    """A row whose INPUT asks for something this port has no API for."""


def _sections(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every w:sectPr of document.xml in document order: its properties without its
    header and footer references, and those as {kind, type, target, tabs}."""
    parts = _written_parts(doc)
    targets = _band_targets(parts)
    root = ET.fromstring(parts["word/document.xml"])
    out = []
    for sect_pr in _collect(root, "sectPr"):
        references = []
        props = []
        for node in list(sect_pr):
            local = _local(node.tag)
            attrs = {_local(k): v for k, v in node.attrib.items()}
            if local in ("headerReference", "footerReference"):
                target = targets.get(attrs["id"])
                part = _part_root(parts.get(f"word/{target}"))
                stops = _collect(part, "tabs") if part is not None else []
                references.append(
                    {
                        "kind": "header" if local == "headerReference" else "footer",
                        "type": attrs.get("type"),
                        "target": target,
                        "tabs": [{_local(k): v for k, v in t.attrib.items()}["pos"] for t in list(stops[0])] if stops else [],
                    }
                )
            else:
                props.append([local, attrs if attrs else True])
        out.append({"props": props, "references": references})
    return out


def _extract(case: dict[str, Any]) -> Any:
    fn = case.get("fn")
    if fn == "readFile":
        return last_word.read(conformance.fixture_bytes(SUITE, case["input"]["file"]))
    if fn not in EXTRACTORS:
        raise RuntimeError(f'no extractor for fn "{fn}"')
    # Since 0.41.0 a row's input is {doc, template?, options?}. This port's
    # to_bytes() takes no write options and cannot write from a template, so a
    # row carrying either is refused here, loudly, rather than written without
    # them -- which would compare a document the row never described.
    extra = sorted(k for k in ("template", "options") if k in case["input"])
    if extra:
        raise _NotApplicable(f"row input carries {extra}; this port has no write options")
    return EXTRACTORS[fn](case["input"]["doc"])


# Rows this port is KNOWN not to pass, each with why. Reported as SKIPS with the
# reason in the summary -- never folded into a pass -- and a ratchet both ways:
# a row listed here that starts passing fails, and so does any other failure.
TEMPLATE_GAP = (
    "templateFirstPageOnly is not applicable: this port cannot write from a template "
    "(to_bytes() takes no template or templateParts option), so there is nothing for "
    "the option to apply to. PHP's `template` write option and `templateParts` (0.13.0, "
    "last-word#5) are unported."
)
BLOCKS_GAP = (
    "a {blocks} header/footer with content: this port's bands are still the one-line "
    "{left, center, right} kind (PHP 0.14.0, last-word#6, unported). An empty "
    '{"blocks": []} variant IS supported and passes in 0063 / 0066.'
)
KNOWN_GAPS: dict[str, str] = {
    "0054-template-letterhead-every-page": TEMPLATE_GAP,
    "0055-template-first-page-only-false": TEMPLATE_GAP,
    "0056-template-first-page-only": TEMPLATE_GAP,
    "0057-template-first-page-only-later-pages": TEMPLATE_GAP,
    "0058-template-first-page-only-uses-its-first-part": TEMPLATE_GAP,
    "0059-template-first-page-only-document-first-wins": TEMPLATE_GAP,
    "0060-read-back-template-first-page-only": TEMPLATE_GAP,
    "0061-first-header-parts": BLOCKS_GAP,
    "0064-read-back-first-and-even": BLOCKS_GAP,
    "0065-fixpoint-first-and-even-bands": BLOCKS_GAP,
    "0069-field-tokens-in-a-blocks-band": BLOCKS_GAP,
}


def _apply_known_gaps(summary: dict[str, Any]) -> dict[str, Any]:
    """Turn each listed row's failure into a named skip; a listed row that passed is a failure."""
    results = []
    for r in summary["results"]:
        reason = KNOWN_GAPS.get(r["id"])
        if reason is None:
            results.append(r)
        elif r["status"] == "fail":
            results.append({"id": r["id"], "title": r.get("title"), "status": "skip", "reason": "known gap: " + reason})
        else:
            results.append(
                {
                    **r,
                    "status": "fail",
                    "expected": "a failure (listed in KNOWN_GAPS)",
                    "actual": f"{r['status']}: remove it from KNOWN_GAPS",
                }
            )
    count = {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "fail", "skip")}
    return {
        **summary,
        "results": results,
        "passed": count["pass"],
        "failed": count["fail"],
        "skipped": count["skip"],
        "ok": count["fail"] == 0,
    }


def test_the_known_gaps_are_real_rows_with_reasons() -> None:
    ids = {c["id"] for c in conformance.cases(SUITE)}
    assert set(KNOWN_GAPS) <= ids, set(KNOWN_GAPS) - ids
    assert all(reason.strip() for reason in KNOWN_GAPS.values())


def test_runs_every_row_in_the_shared_table(capsys: pytest.CaptureFixture[str]) -> None:
    summary = _apply_known_gaps(conformance.run_table(SUITE, _extract, language="python"))
    # Printed unconditionally: a bare "3 skipped" in a log reads identically to
    # full coverage at a glance, so every skip is named with its reason. Past
    # pytest's capture, or a passing test's print() never reaches the CI log.
    with capsys.disabled():
        print("\n" + conformance.format_summary(summary))
    assert summary["ok"], "\n" + conformance.format_summary(summary)


def test_compared_something_the_loop_is_not_empty() -> None:
    # Without this, an empty or unloadable table reports success over zero
    # assertions, which is worse than a red build because nobody investigates
    # green.
    cases = conformance.cases(SUITE)
    assert len(cases) > 40
    for case in cases:
        assert case.get("fn") in EXTRACTORS or case.get("fn") == "readFile", f'row declares fn "{case.get("fn")}" with no extractor'


def test_the_extractors_discriminate() -> None:
    # The control. Every extractor is a projection, and a projection that
    # returns a constant passes every row it is given.
    shaded = EXTRACTORS["cellProps"](
        {"blocks": [{"type": "table", "rows": [{"cells": [{"blocks": [], "shading": "#123456"}]}]}]}
    )
    plain = EXTRACTORS["cellProps"](
        {"blocks": [{"type": "table", "rows": [{"cells": [{"blocks": []}]}]}]}
    )
    assert shaded != plain

    noted = EXTRACTORS["footnotesPart"](
        {"blocks": [{"type": "paragraph", "runs": [{"text": "", "footnote": {"blocks": [{"type": "paragraph", "runs": [{"text": "n"}]}]}}]}]}
    )
    bare = EXTRACTORS["footnotesPart"]({"blocks": [{"type": "paragraph", "runs": [{"text": "n"}]}]})
    assert noted != bare
    assert noted["references"] == ["1"] and bare["references"] == []

    first = EXTRACTORS["bandParts"](
        {"page": {"header": {"left": "Rest"}, "firstHeader": {"left": "First"}}, "blocks": []}
    )
    plain = EXTRACTORS["bandParts"]({"page": {"header": {"left": "Rest"}}, "blocks": []})
    assert first != plain and first["titlePg"] is True and plain["titlePg"] is False

    dated = EXTRACTORS["bandFields"]({"page": {"footer": {"left": "{date:yyyy}"}}, "blocks": []})
    undated = EXTRACTORS["bandFields"]({"page": {"footer": {"left": "{date}"}}, "blocks": []})
    assert dated != undated

    authored = EXTRACTORS["coreProps"]({"title": "T", "meta": {"author": "A"}, "blocks": []})
    titled = EXTRACTORS["coreProps"]({"title": "T", "blocks": []})
    assert authored != titled and authored["elements"][0] == ["dc:title", "T"]
    assert EXTRACTORS["coreProps"]({"blocks": []}) is None

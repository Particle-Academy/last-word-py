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

EXTRACTORS = {
    "runProps": _run_props,
    "paragraphProps": lambda doc: [
        _norm_props(_child(p, "pPr")) for p in _collect(_document_xml(doc), "p")
    ],
    "tableProps": _table_props,
    "cellProps": lambda doc: [
        _norm_props(_child(tc, "tcPr")) for tc in _collect(_document_xml(doc), "tc")
    ],
    "sectionProps": lambda doc: _norm_props(_child(_child(_document_xml(doc), "body"), "sectPr")),
    "readBack": lambda doc: last_word.read(last_word.to_bytes(doc)),
    # The comparator is the suite's own, so "equal" means the same thing here
    # as it does for every other row.
    "roundTripFixpoint": lambda doc: {
        "fixpoint": conformance.equals(last_word.read(last_word.to_bytes(doc)), doc)
    },
}


def _extract(case: dict[str, Any]) -> Any:
    fn = case.get("fn")
    if fn not in EXTRACTORS:
        raise RuntimeError(f'no extractor for fn "{fn}"')
    return EXTRACTORS[fn](case["input"]["doc"])


def test_runs_every_row_in_the_shared_table(capsys: pytest.CaptureFixture[str]) -> None:
    summary = conformance.run_table(SUITE, _extract, language="python")
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
        assert case.get("fn") in EXTRACTORS, f'row declares fn "{case.get("fn")}" with no extractor'


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

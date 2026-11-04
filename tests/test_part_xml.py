"""Every XML part of an uploaded file is read through one gate, which refuses a DOCTYPE.

Most of this goes through the public reader, because what matters is what a hostile
upload gets back. The same fixtures as PHP's `PartXmlTest` and Node's
`doctype-refusal.test.ts`. MARKER is the visible text of each part in a clean file,
and ENTITY the value its replacement declares, so ENTITY in the result means it
expanded.
"""

from __future__ import annotations

import ast
import io
import json
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

import last_word

MARKER = "LWBASE"
ENTITY = "LWINT"
DOCTYPE = f'<!DOCTYPE r [<!ENTITY x "{ENTITY}">]>'
FORMATS = Path(__file__).parent / "data" / "formats"
SRC = Path(__file__).parent.parent / "src" / "last_word"


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {name: z.read(name) for name in z.namelist()}


def rezip(parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, body in parts.items():
            z.writestr(name, body)
    return out.getvalue()


def docx() -> dict[str, bytes]:
    return unzip(
        last_word.to_bytes(
            {
                "title": MARKER,
                "defaultFont": MARKER,
                "page": {"header": {"left": MARKER}},
                "blocks": [
                    {
                        "type": "paragraph",
                        "runs": [
                            {"text": MARKER},
                            {"text": "", "footnote": {"blocks": [{"type": "paragraph", "runs": [{"text": MARKER}]}]}},
                        ],
                    },
                    {
                        "type": "chart",
                        "option": {
                            "title": {"text": MARKER},
                            "xAxis": {"type": "category", "data": ["a"]},
                            "yAxis": {"type": "value"},
                            "series": [{"type": "bar", "data": [1]}],
                        },
                    },
                ],
            }
        )
    )


def with_doctype(xml: str, doctype: str = DOCTYPE) -> str:
    m = re.match(r"<\?xml[^>]*\?>\s*", xml)
    return m.group(0) + doctype + xml[m.end() :] if m else doctype + xml


def declare(xml: str, encoding: str) -> str:
    return re.sub(r"^<\?xml[^>]*\?>", f'<?xml version="1.0" encoding="{encoding}"?>', xml)


ENCODINGS: dict[str, Callable[[str], bytes]] = {
    "plain UTF-8": lambda x: x.encode("utf-8"),
    "UTF-8 with a BOM": lambda x: b"\xef\xbb\xbf" + x.encode("utf-8"),
    "UTF-16LE with a BOM": lambda x: b"\xff\xfe" + declare(x, "UTF-16").encode("utf-16-le"),
    "UTF-16BE with a BOM": lambda x: b"\xfe\xff" + declare(x, "UTF-16").encode("utf-16-be"),
    "UTF-16LE without a BOM": lambda x: declare(x, "UTF-16").encode("utf-16-le"),
    "past a long comment": lambda x: re.sub(r"^(<\?xml[^>]*\?>\s*)", lambda m: m.group(1) + "<!-- " + "p" * 70000 + " -->", x).encode("utf-8"),
}
PARTS = ["word/document.xml", "word/header1.xml", "word/footnotes.xml", "docProps/core.xml", "word/styles.xml", "word/charts/chart1.xml"]
CASES = [(p, e) for p in PARTS for e in ENCODINGS]


def read(parts: dict[str, bytes]) -> str:
    try:
        return json.dumps(last_word.read(rezip(parts)))
    except RuntimeError:
        return "(refused)"


def test_reads_the_marker_from_every_part_of_a_clean_file() -> None:
    assert read(docx()).count(MARKER) == 6


def test_the_fixture_entity_is_live() -> None:
    # So its absence below is the gate and not the fixture.
    assert ET.fromstring(f"{DOCTYPE}<r>&x;</r>").text == ENTITY


@pytest.mark.parametrize(("part", "encoding"), CASES)
def test_expands_no_entity(part: str, encoding: str) -> None:
    parts = docx()
    parts[part] = ENCODINGS[encoding](with_doctype(parts[part].decode("utf-8").replace(MARKER, "&x;")))
    assert ENTITY not in read(parts)


@pytest.mark.parametrize(("part", "encoding"), CASES)
def test_still_reads_each_encoding_without_a_doctype(part: str, encoding: str) -> None:
    parts = docx()
    parts[part] = ENCODINGS[encoding](parts[part].decode("utf-8"))
    assert read(parts).count(MARKER) == 6


@pytest.mark.parametrize(("part", "encoding"), CASES)
def test_takes_nothing_from_a_part_declaring_a_doctype(part: str, encoding: str) -> None:
    parts = docx()
    parts[part] = ENCODINGS[encoding](with_doctype(parts[part].decode("utf-8")))
    assert read(parts).count(MARKER) < 6


def test_a_refused_body_part_behaves_as_an_unparsable_one() -> None:
    refused = docx()
    refused["word/document.xml"] = with_doctype(refused["word/document.xml"].decode("utf-8")).encode("utf-8")
    unparsable = docx()
    unparsable["word/document.xml"] = b"<w:document>"
    assert read(refused) == read(unparsable)


def test_follows_no_relationship_whose_target_comes_from_an_entity() -> None:
    parts = docx()
    rels = parts["word/_rels/document.xml.rels"].decode("utf-8").replace('Target="header1.xml"', 'Target="&x;"')
    parts["word/_rels/document.xml.rels"] = ENCODINGS["UTF-16LE with a BOM"](with_doctype(rels, '<!DOCTYPE r [<!ENTITY x "header1.xml">]>'))
    assert "header" not in json.loads(read(parts)).get("page", {})


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_reads_no_settings_from_a_part_declaring_a_doctype(encoding: str) -> None:
    parts = unzip(last_word.to_bytes({"page": {"header": {"left": "odd"}, "evenHeader": {"left": "even"}}, "blocks": []}))
    assert "evenHeader" in json.loads(read(parts))["page"]
    parts["word/settings.xml"] = ENCODINGS[encoding](with_doctype(parts["word/settings.xml"].decode("utf-8")))
    assert "evenHeader" not in json.loads(read(parts))["page"]


def test_reads_nothing_of_a_file_a_system_entity_names(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch.txt"
    scratch.write_text("SCRATCHFILE", encoding="utf-8")
    doctype = f'<!DOCTYPE r [<!ENTITY x SYSTEM "{scratch.as_uri()}">]>'
    for part in PARTS:
        parts = docx()
        parts[part] = with_doctype(parts[part].decode("utf-8").replace(MARKER, "&x;"), doctype).encode("utf-8")
        assert "SCRATCHFILE" not in read(parts)


@pytest.mark.parametrize("part", ["content.xml", "styles.xml"])
@pytest.mark.parametrize("encoding", ENCODINGS)
def test_refuses_a_doctype_in_an_odt_part_by_name(part: str, encoding: str) -> None:
    odt = unzip((FORMATS / "report.odt").read_bytes())
    odt[part] = ENCODINGS[encoding](with_doctype(odt[part].decode("utf-8")))
    with pytest.raises(RuntimeError, match="DOCTYPE"):
        last_word.read(rezip(odt))


# ---------------- the gate on its own ----------------


@pytest.mark.parametrize(
    "xml",
    [
        f"{DOCTYPE}<r/>",
        f'<?xml version="1.0"?>{DOCTYPE}<r/>',
        f'<?xml version="1.0"?>\n <!-- c -->\n{DOCTYPE}<r/>',
        f"﻿{DOCTYPE}<r/>",
        f"<!-- {'p' * 100000} -->{DOCTYPE}<r/>",
        f"<?pi a?>{DOCTYPE}<r/>",
    ],
    ids=["bare", "after a declaration", "after a comment", "after a BOM", "past a long comment", "after a processing instruction"],
)
def test_the_prolog_check_finds_one(xml: str) -> None:
    from last_word.archive.part_xml import prolog_declares_doctype

    assert prolog_declares_doctype(xml)
    assert prolog_declares_doctype(xml.encode("utf-8"))


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe" + f"{DOCTYPE}<r/>".encode("utf-16-le"),
        b"\xfe\xff" + f"{DOCTYPE}<r/>".encode("utf-16-be"),
        f"{DOCTYPE}<r/>".encode("utf-16-le"),
        f"{DOCTYPE}<r/>".encode("utf-16-be"),
    ],
)
def test_the_prolog_check_finds_one_in_utf16(data: bytes) -> None:
    from last_word.archive.part_xml import prolog_declares_doctype

    assert prolog_declares_doctype(data)


@pytest.mark.parametrize(
    "xml",
    [
        "",
        "<r/>",
        "<r><![CDATA[<!DOCTYPE html>]]></r>",
        "<!-- <!DOCTYPE x> --><r/>",
        "<?pi <!DOCTYPE x>?><r/>",
        f"<!-- {DOCTYPE}",
        f"<?xml {DOCTYPE}",
    ],
)
def test_the_prolog_check_finds_none(xml: str) -> None:
    from last_word.archive.part_xml import prolog_declares_doctype

    assert not prolog_declares_doctype(xml)


def test_the_gate_reads_text_that_merely_mentions_a_doctype() -> None:
    from last_word.archive.part_xml import parse

    root = parse(b"<r><![CDATA[<!DOCTYPE html>]]></r>")
    assert root is not None and root.text == "<!DOCTYPE html>"


def test_the_gate_asks_the_parser_when_the_prolog_check_cannot_read_the_encoding() -> None:
    from last_word.archive import part_xml

    # With the walk blind, the parser alone meets the DOCTYPE and decides.
    hidden = DOCTYPE.encode() + b"<r>&x;</r>"
    original = part_xml.prolog_declares_doctype
    part_xml.prolog_declares_doctype = lambda _xml: False  # type: ignore[assignment]
    try:
        assert part_xml.parse(hidden) is None
        assert part_xml.declares_doctype(hidden)
    finally:
        part_xml.prolog_declares_doctype = original  # type: ignore[assignment]


def test_the_gate_fails_malformed_input_the_way_it_fails_a_doctype() -> None:
    from last_word.archive.part_xml import declares_doctype, parse

    for bad in (b"<r>", DOCTYPE.encode() + b"<r/>", b"", None, b'<?xml version="1.0" encoding="UTF-7"?><r/>'):
        assert parse(bad) is None
    assert not declares_doctype(b"<r>")


def test_parse_dom_refuses_too() -> None:
    from last_word.reader.odt.xml_dom import parse_dom

    with pytest.raises(ValueError):
        parse_dom(f'<?xml version="1.0"?>{DOCTYPE}<r/>')
    with pytest.raises(ValueError):
        parse_dom("��" + "\0".join(f"{DOCTYPE}<r/>") + "\0")


# ---------------- nothing else parses ----------------


def bypasses(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "part_xml.py":
            continue
        tree: Any = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("fromstring", "XML", "XMLParser", "iterparse", "fromstringlist"):
                found.append(f"{path.name}: {node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "parse" and isinstance(node.func.value, ast.Name) and node.func.value.id == "ET":
                found.append(f"{path.name}: parse")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                if any(n.split(".")[0] == "lxml" or n.startswith(("xml.dom", "xml.sax", "xml.parsers")) for n in names):
                    found.append(f"{path.name}: {names[-1] or names[0]}")
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
                value = node.value if isinstance(node.value, bytes) else node.value.encode("utf-8", "replace")
                if value.lower() == b"<!doctype":
                    found.append(f"{path.name}: a byte scan")
    return found


def test_leaves_no_way_to_a_part_except_through_the_gate() -> None:
    assert bypasses(SRC) == []


def test_whose_check_can_see_one(tmp_path: Path) -> None:
    (tmp_path / "sneak.py").write_text('from xml.etree import ElementTree as ET\nx = ET.fromstring(b"<r/>")\ny = ET.parse("f")\nz = b"<!doctype" in x\n', encoding="utf-8")
    assert sorted(bypasses(tmp_path)) == ["sneak.py: a byte scan", "sneak.py: fromstring", "sneak.py: parse"]

"""Is the container a well-formed OPC package?

The parity suite proves this port writes the same parts as PHP. It cannot prove
those parts are *valid*, because if both engines emit the same broken package
they still agree. This file is the independent half, and it depends on no other
runtime.

It checks the three things that actually produce Word/Excel/PowerPoint's
"we found a problem with some content" repair prompt, in the order they bite:

1. **A part with no content type.** Every part must be covered by a `Default`
   for its extension or an `Override` for its exact name. This is the single
   most common cause of a repair prompt and the easiest to introduce -- adding
   an image without adding its `Default`.
2. **A dangling relationship.** An internal `Relationship` whose `Target` is not
   in the archive. Office follows these, and a broken one is fatal rather than
   ignored.
3. **Malformed XML.** Rare from a string builder, but an unescaped `&` in a
   caption produces it, and the failure is total.

A repair prompt is a failed write even when the document eventually opens, so
these are assertions and not warnings.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from xml.etree import ElementTree

import pytest

from last_word import to_bytes
from tests.fixtures import DOCS

CONTENT_TYPES = "[Content_Types].xml"
CT_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
XML_SUFFIXES = (".xml", ".rels", ".vml")


def _package(payload: object) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(to_bytes(payload)))


@pytest.fixture(params=sorted(DOCS), ids=sorted(DOCS))
def package(request) -> zipfile.ZipFile:
    return _package(DOCS[request.param])


def test_every_part_has_a_content_type(package: zipfile.ZipFile) -> None:
    root = ElementTree.fromstring(package.read(CONTENT_TYPES))
    defaults = {
        (node.get("Extension") or "").lower()
        for node in root.iter(f"{CT_NS}Default")
    }
    overrides = {
        (node.get("PartName") or "").lstrip("/")
        for node in root.iter(f"{CT_NS}Override")
    }

    for name in package.namelist():
        if name == CONTENT_TYPES:
            continue
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        assert extension in defaults or name in overrides, (
            f"{name} has no content type. Office reports this as a corrupt file and "
            "offers to repair it."
        )


def test_no_relationship_dangles(package: zipfile.ZipFile) -> None:
    names = set(package.namelist())

    for part in package.namelist():
        if not part.endswith(".rels"):
            continue
        base = posixpath.dirname(posixpath.dirname(part))  # strip "_rels"

        for node in ElementTree.fromstring(package.read(part)).iter(f"{REL_NS}Relationship"):
            if (node.get("TargetMode") or "Internal") == "External":
                continue
            target = node.get("Target") or ""
            resolved = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join(base, target))
            )
            assert resolved in names, (
                f"{part} points at {target!r} ({resolved}), which is not in the package"
            )


def test_relationship_ids_are_unique_within_a_part() -> None:
    """A duplicate `Id` in one `.rels` part makes the package malformed.

    An `r:id` reference resolves through that table, so two entries sharing an
    id means a reference whose target depends on which one the reader picks.

    This is not a hypothetical. `dark-slide` — this package's pptx sibling —
    allocates image relationship ids from a GLOBAL media counter while a
    slide's own layout relationship is also `rId1`, so **every deck containing
    an image** emits a duplicate, in both the PHP and the Node engine. It is
    checked here because the same shape of bug (one counter feeding two id
    spaces) is what a writer with hyperlink rels and image rels is one refactor
    away from.
    """
    for name in sorted(DOCS):
        package = _package(DOCS[name])
        for part in package.namelist():
            if not part.endswith(".rels"):
                continue
            ids = [
                node.get("Id")
                for node in ElementTree.fromstring(package.read(part)).iter(f"{REL_NS}Relationship")
            ]
            duplicates = {i for i in ids if ids.count(i) > 1}
            assert not duplicates, f"{name}: {part} reuses relationship id(s) {duplicates}"


def test_every_xml_part_parses(package: zipfile.ZipFile) -> None:
    for name in package.namelist():
        if not name.endswith(XML_SUFFIXES):
            continue
        try:
            ElementTree.fromstring(package.read(name))
        except ElementTree.ParseError as error:  # pragma: no cover - the assert is the report
            pytest.fail(f"{name} is not well-formed XML: {error}")


def test_the_package_has_a_root_relationship(package: zipfile.ZipFile) -> None:
    assert "_rels/.rels" in package.namelist(), "an OPC package without _rels/.rels is not a package"

    targets = [
        node.get("Target") or ""
        for node in ElementTree.fromstring(package.read("_rels/.rels")).iter(f"{REL_NS}Relationship")
        if (node.get("Type") or "").endswith("/officeDocument")
    ]
    assert targets, "no officeDocument relationship -- nothing tells the reader where to start"

    names = set(package.namelist())
    for target in targets:
        assert target.lstrip("/") in names, f"the officeDocument target {target!r} is missing"


def test_part_names_are_wellformed(package: zipfile.ZipFile) -> None:
    names = package.namelist()

    assert len(names) == len(set(names)), "a duplicate part name makes the package ambiguous"
    for name in names:
        assert not name.startswith("/"), f"{name} is stored with a leading slash"
        assert "\\" not in name, f"{name} uses a backslash separator (Windows path leaked in)"
        assert not name.endswith("/"), f"{name} is a directory entry; OPC packages carry parts only"


def test_the_fixture_table_is_not_empty() -> None:
    """Without this every parametrised test above would vanish silently."""
    assert len(DOCS) >= 4

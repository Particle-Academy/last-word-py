"""The one way an XML part of an uploaded file gets parsed. Mirrors PHP `Archive\\PartXml`.

A part carrying a DOCTYPE is refused, because no Office or ODF part has one and an
internal subset is the entry point for entity expansion. A refusal looks exactly like
malformed XML, so each caller keeps its own handling of a part it cannot read.

The check has two halves, and the second is the one that decides:

- `prolog_declares_doctype` looks at the bytes before anything is parsed. It reads
  only the prolog: searching the whole part for the word would refuse legitimate text.
- The parser is then asked: a `TreeBuilder` whose `doctype` callback stops the parse
  the moment expat reports a declaration, before any entity in the body is reached.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET


class _Refused(Exception):
    """A doctype declaration reached the parser."""


class _RefusingBuilder(ET.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise _Refused


def _parse(raw: bytes) -> ET.Element:
    parser = ET.XMLParser(target=_RefusingBuilder())
    parser.feed(raw)
    return parser.close()


def _bytes(xml: bytes | str) -> bytes:
    return xml.encode("utf-8", "replace") if isinstance(xml, str) else xml


def parse(xml: bytes | str | None) -> ET.Element | None:
    """The part's root element, or None when absent, empty, malformed or refused."""
    if xml is None or xml == b"" or xml == "" or prolog_declares_doctype(xml):
        return None
    try:
        return _parse(_bytes(xml))
    except (_Refused, ET.ParseError, ValueError, LookupError):
        return None


def declares_doctype(xml: bytes | str) -> bool:
    """Whether a part declares a DOCTYPE. Malformed XML does not, however it fails."""
    if xml == b"" or xml == "":
        return False
    if prolog_declares_doctype(xml):
        return True
    try:
        _parse(_bytes(xml))
    except _Refused:
        return True
    except (ET.ParseError, ValueError, LookupError):
        return False
    return False


def prolog_declares_doctype(xml: bytes | str) -> bool:
    """Whether a DOCTYPE comes before the root element, for what a walk can read."""
    text = xml.decode("latin-1") if isinstance(xml, bytes) else xml
    if "\0" in text[:4]:
        text = text.replace("\0", "")
    at = 0
    for bom in ("\xef\xbb\xbf", "\xff\xfe", "\xfe\xff", "﻿", "��", "�"):
        if text.startswith(bom):
            at = len(bom)
            break
    while True:
        while at < len(text) and text[at] in " \t\r\n":
            at += 1
        if text.startswith("<?", at):
            close = "?>"
        elif text.startswith("<!--", at):
            close = "-->"
        else:
            return text[at : at + 9].upper() == "<!DOCTYPE"
        end = text.find(close, at)
        if end < 0:
            return False
        at = end + len(close)

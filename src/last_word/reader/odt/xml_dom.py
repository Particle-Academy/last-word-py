"""A small XML parser that keeps what an ODF reader needs.

Mirrors Node `reader/odt/xml-dom.ts`. This module has no PHP counterpart because
PHP has DOM; it exists here for two reasons `xml.etree.ElementTree` cannot meet:

- **qualified names.** The PHP engine looks attributes up as written
  (`getAttribute('text:style-name')`). ElementTree resolves prefixes to
  namespace URIs and forgets them, so the same lookup would need a different
  rule here, and three engines with three rules disagree on the one file that
  binds an unusual prefix.
- **the same refusals as libxml.** An element nested deeper than 257 levels is
  refused, as libxml 2.11 refuses it without XML_PARSE_HUGE. That bound also
  keeps every recursive walk in the reader far below Python's frame limit.

Text and elements keep their order (`a<text:s/>b`). Only the five predefined
entities and character references are expanded; a DOCTYPE is refused before
this runs, and any other markup declaration is an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

#: The deepest element nesting libxml 2.11 parses without XML_PARSE_HUGE
#: (measured: 257 opens, 258 fails).
MAX_ELEMENT_DEPTH = 257


@dataclass
class Element:
    name: str
    local: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)


Node = Union[Element, str]

_ATTR = re.compile(r"""([^\s=]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_ENTITY = re.compile(r"&(#x[0-9a-fA-F]+|#[0-9]+|[A-Za-z]+);")
_PREDEFINED = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def parse_dom(src: str) -> Element:
    n = len(src)
    root = Element("#document", "#document")
    stack: list[Element] = [root]
    i = 0

    def text(s: str) -> None:
        if s == "":
            return
        parent = stack[-1]
        if parent is root:
            if re.search(r"[^ \t\r\n]", s):
                raise ValueError("text outside the root element")
            return
        if parent.children and isinstance(parent.children[-1], str):
            parent.children[-1] += s
        else:
            parent.children.append(s)

    while i < n:
        if src[i] != "<":
            stop = src.find("<", i)
            stop = n if stop < 0 else stop
            text(_unescape(_newlines(src[i:stop])))
            i = stop
            continue
        if src.startswith("<!--", i):
            end = src.find("-->", i + 4)
            if end < 0:
                raise ValueError("unterminated comment")
            i = end + 3
            continue
        if src.startswith("<![CDATA[", i):
            end = src.find("]]>", i + 9)
            if end < 0:
                raise ValueError("unterminated CDATA section")
            text(_newlines(src[i + 9 : end]))
            i = end + 3
            continue
        if src.startswith("<?", i):
            end = src.find("?>", i + 2)
            if end < 0:
                raise ValueError("unterminated processing instruction")
            i = end + 2
            continue
        if src.startswith("<!", i):
            raise ValueError("markup declarations are not allowed")
        if src.startswith("</", i):
            end = src.find(">", i + 2)
            if end < 0:
                raise ValueError("unterminated end tag")
            name = src[i + 2 : end].strip()
            if len(stack) == 1 or stack[-1].name != name:
                raise ValueError(f"mismatched end tag {name}")
            stack.pop()
            i = end + 1
            continue

        end = _tag_end(src, i + 1)
        if end < 0:
            raise ValueError("unterminated start tag")
        inner = src[i + 1 : end]
        self_closing = inner.endswith("/")
        if self_closing:
            inner = inner[:-1]
        element = _start_tag(inner)
        if len(stack) > MAX_ELEMENT_DEPTH:
            raise ValueError("the document nests elements too deeply")
        parent = stack[-1]
        if parent is root and root.children:
            raise ValueError("more than one root element")
        parent.children.append(element)
        if not self_closing:
            stack.append(element)
        i = end + 1

    if len(stack) != 1 or not root.children or not isinstance(root.children[0], Element):
        raise ValueError("the document is incomplete")
    return root.children[0]


def _newlines(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _tag_end(src: str, start: int) -> int:
    quote = ""
    for i in range(start, len(src)):
        ch = src[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ('"', "'"):
            quote = ch
        elif ch == ">":
            return i
    return -1


def _start_tag(inner: str) -> Element:
    match = re.match(r"\s*(\S+)", inner)
    if not match:
        raise ValueError("empty start tag")
    name = match.group(1)
    attrs: dict[str, str] = {}
    for a in _ATTR.finditer(inner, match.end()):
        key = a.group(1)
        raw = a.group(2) if a.group(2) is not None else (a.group(3) or "")
        # Attribute-value normalisation: literal white space becomes a space.
        value = re.sub(r"[\t\n\r]", " ", _newlines(raw))
        if key not in attrs:
            attrs[key] = _unescape(value)
    colon = name.find(":")
    return Element(name, name[colon + 1 :] if colon >= 0 else name, attrs)


def _unescape(s: str) -> str:
    if "&" not in s:
        return s

    def entity(m: re.Match[str]) -> str:
        ent = m.group(1)
        if ent in _PREDEFINED:
            return _PREDEFINED[ent]
        if not ent.startswith("#"):
            raise ValueError(f"undefined entity {m.group(0)}")
        code = int(ent[2:], 16) if ent[1] == "x" else int(ent[1:])
        if not (code in (0x9, 0xA, 0xD) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF):
            raise ValueError(f"invalid character reference {m.group(0)}")
        return chr(code)

    return _ENTITY.sub(entity, s)


def text_content(node: Node) -> str:
    """All descendant text, as DOM `textContent` -- walked with an explicit stack."""
    out: list[str] = []
    stack: list[Node] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            out.append(current)
        else:
            stack.extend(reversed(current.children))
    return "".join(out)

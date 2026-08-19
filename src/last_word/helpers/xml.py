"""Tiny XML-escaping helpers -- the mirror of PHP's `LastWord\\Helpers\\Xml`.

The writer builds XML by **string concatenation**, not by serialising an
`ElementTree`. That is not laziness: byte-parity with the PHP engine is this
port's acceptance test, and `ElementTree.tostring` reorders nothing reliably,
picks its own self-closing style, emits its own namespace prefixes and escapes a
different character set. It cannot reproduce the reference bytes. Verbose
WordprocessingML is also far clearer as a template than as a tree of
`SubElement` calls, which is why all three engines write it this way.
"""

from __future__ import annotations


def text(s: str) -> str:
    """Escape for XML text content.

    Mirrors `htmlspecialchars($s, ENT_XML1 | ENT_COMPAT, 'UTF-8')`: `&`, `<`,
    `>` and `"` -- and deliberately NOT `'`, which ENT_COMPAT leaves alone.
    `&` goes first or the ampersands of the other replacements get re-escaped.
    """
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def attr(s: str) -> str:
    """Escape for XML attribute values.

    Mirrors `htmlspecialchars($s, ENT_XML1 | ENT_QUOTES, 'UTF-8')` -- the same
    set as `text()` plus `'`, which in XML1 mode becomes `&apos;` (in HTML mode
    PHP would emit `&#039;`; the difference is visible in output bytes).
    """
    return text(s).replace("'", "&apos;")


def declaration(standalone: bool = True) -> str:
    """The XML declaration at the top of every DOCX part."""
    sa = ' standalone="yes"' if standalone else ""
    return f'<?xml version="1.0" encoding="UTF-8"{sa}?>\n'

"""GFM markdown -> Doc model. The Editor bridge's inbound half.

A hand-rolled recursive-descent parser (no markdown dependency) covering the
subset `to_markdown` emits:

  `#` headings, paragraphs, `**bold**` / `*italic*` / `_italic_` / `~~strike~~` /
  `` `code` `` / `[text](url)` inline, ordered + unordered nested lists (2-space
  indent per level), GFM tables, fenced code blocks, `>` blockquotes, standalone
  `![alt](src)` images, `---` hr and the `<!-- pagebreak -->` comment convention.

The result never has a `title` -- markdown has no title slot; a leading `#` line
stays a level-1 heading block.

## String indexing

PHP indexes by BYTE and this port by CODEPOINT, and that is deliberate rather
than a difference to paper over: every delimiter this parser scans for (`*`, `` ` ``,
`~`, `[`, `\\`) is ASCII, and in UTF-8 no ASCII byte can occur inside a
multi-byte sequence. Offsets therefore agree wherever they are compared, and
codepoint indexing is the family's chosen rule (`documents.md` §2.2).

Patterns are compiled with `re.ASCII` because the PHP originals carry no `/u`
modifier, so their `\\s` / `\\d` / `\\S` classes are ASCII-only. Leaving Python's
Unicode-aware defaults on would quietly make a non-breaking space a valid
heading separator here and not in PHP.
"""

from __future__ import annotations

import re
from typing import Any

from ..helpers.php import php_rtrim, php_trim

_FENCE_RE = re.compile(r"^```(.*)$", re.ASCII)
_FENCE_CLOSE_RE = re.compile(r"^```\s*$", re.ASCII)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.ASCII)
_PAGEBREAK_RE = re.compile(r"^<!--\s*page\s?break\s*-->\s*$", re.ASCII | re.IGNORECASE)
_HR_RE = re.compile(r"^ {0,3}(-{3,}|\*{3,}|_{3,})\s*$", re.ASCII)
_QUOTE_RE = re.compile(r"^ {0,3}>", re.ASCII)
_QUOTE_STRIP_RE = re.compile(r"^ {0,3}> ?", re.ASCII)
_HEADING_START_RE = re.compile(r"^(#{1,6})\s+", re.ASCII)
_BULLET_RE = re.compile(r"^([ \t]*)([-*+])\s+(\S.*)$", re.ASCII)
_ORDERED_RE = re.compile(r"^([ \t]*)(\d{1,9})[.)]\s+(\S.*)$", re.ASCII)
_SEPARATOR_RE = re.compile(r"^\|?[\s:|-]+\|?$", re.ASCII)
_IMAGE_LINE_RE = re.compile(
    r'^!\[((?:\\.|[^\]\\])*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)\s*$', re.ASCII
)
_LINK_RE = re.compile(r'\[((?:\\.|[^\]\\])*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)', re.ASCII)
_INLINE_IMAGE_RE = re.compile(
    r'!\[((?:\\.|[^\]\\])*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)', re.ASCII
)
_WHITESPACE_RUN_RE = re.compile(r"\s+", re.ASCII)


class FromMarkdown:
    """Mirrors `LastWord\\Markdown\\FromMarkdown`."""

    def convert(self, markdown: str) -> dict[str, Any]:
        normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
        return {"blocks": self._parse_blocks(normalized.split("\n"))}

    # ─── Blocks ──────────────────────────────────────────────────────────

    def _parse_blocks(self, lines: list[str]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        i = 0
        count = len(lines)

        while i < count:
            line = lines[i]

            if php_trim(line) == "":
                i += 1
                continue

            # Fenced code
            match = _FENCE_RE.match(line)
            if match is not None:
                language = php_trim(match.group(1))
                language = _WHITESPACE_RUN_RE.split(language)[0] if language != "" else ""
                code_lines: list[str] = []
                i += 1
                while i < count and _FENCE_CLOSE_RE.match(lines[i]) is None:
                    code_lines.append(lines[i])
                    i += 1
                i += 1  # consume the closing fence (or EOF)
                block: dict[str, Any] = {"type": "code"}
                if language != "":
                    block["language"] = language
                block["text"] = "\n".join(code_lines)
                blocks.append(block)
                continue

            # Heading
            match = _HEADING_RE.match(line)
            if match is not None:
                blocks.append(
                    {
                        "type": "heading",
                        "level": len(match.group(1)),
                        "runs": self._inline(php_rtrim(match.group(2))),
                    }
                )
                i += 1
                continue

            # Page break comment
            if _PAGEBREAK_RE.match(line) is not None:
                blocks.append({"type": "pageBreak"})
                i += 1
                continue

            # Thematic break -- checked before lists (`-` overlaps)
            if _HR_RE.match(line) is not None:
                blocks.append({"type": "hr"})
                i += 1
                continue

            # Blockquote
            if _QUOTE_RE.match(line) is not None:
                quote_lines: list[str] = []
                while i < count and _QUOTE_RE.match(lines[i]) is not None:
                    quote_lines.append(_QUOTE_STRIP_RE.sub("", lines[i], count=1))
                    i += 1
                blocks.append({"type": "quote", "blocks": self._parse_blocks(quote_lines)})
                continue

            # List
            if _match_list_line(line) is not None:
                entries: list[dict[str, Any]] = []
                while i < count:
                    entry = _match_list_line(lines[i])
                    if entry is None:
                        break
                    entries.append(entry)
                    i += 1
                blocks.append(self._assemble_list(entries))
                continue

            # Table: a pipe row followed by a separator row. "Contains a pipe",
            # not "starts with one" -- GFM tables commonly omit the leading pipe.
            if "|" in line and i + 1 < count and _is_table_separator(lines[i + 1]):
                rows: list[dict[str, Any]] = [
                    {"header": True, "cells": self._split_table_row(line)}
                ]
                i += 2  # skip header + separator
                while i < count and php_trim(lines[i]) != "" and "|" in lines[i]:
                    rows.append({"cells": self._split_table_row(lines[i])})
                    i += 1
                blocks.append({"type": "table", "rows": rows})
                continue

            # Standalone image
            match = _IMAGE_LINE_RE.match(php_trim(line))
            if match is not None:
                block = {"type": "image", "src": match.group(2)}
                alt = match.group(1).replace("\\[", "[").replace("\\]", "]")
                if alt != "":
                    block["alt"] = alt
                blocks.append(block)
                i += 1
                continue

            # Paragraph: consume until a blank line or a new block form
            para_lines = [line]
            i += 1
            while i < count and php_trim(lines[i]) != "" and not _starts_block(lines[i]):
                para_lines.append(lines[i])
                i += 1
            blocks.append(
                {
                    "type": "paragraph",
                    "runs": self._inline(" ".join(php_trim(p) for p in para_lines)),
                }
            )

        return blocks

    # ─── Lists ───────────────────────────────────────────────────────────

    def _assemble_list(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        block: dict[str, Any] = {"type": "list"}
        if entries[0]["ordered"]:
            block["ordered"] = True
        items: list[dict[str, Any]] = []
        block["items"] = items

        stack: list[list[dict[str, Any]]] = [items]

        for entry in entries:
            depth = min(entry["depth"], len(stack))
            while len(stack) - 1 > depth:
                stack.pop()
            parent = stack[-1]
            if depth > len(stack) - 1 and parent:
                last = parent[-1]
                if "children" not in last:
                    last["children"] = []
                stack.append(last["children"])
                parent = stack[-1]
            parent.append({"runs": self._inline(entry["content"])})

        return block

    # ─── Tables ──────────────────────────────────────────────────────────

    def _split_table_row(self, line: str) -> list[dict[str, Any]]:
        trimmed = php_trim(line)
        trimmed = trimmed.removeprefix("|")
        trimmed = trimmed.removesuffix("|")

        cells: list[dict[str, Any]] = []
        for cell_text in _split_unescaped_pipes(trimmed):
            cell_text = php_trim(cell_text.replace("\\|", "|"))
            cells.append(
                {
                    "blocks": []
                    if cell_text == ""
                    else [{"type": "paragraph", "runs": self._inline(cell_text)}]
                }
            )
        return cells

    # ─── Inline ──────────────────────────────────────────────────────────

    def _inline(self, text: str, flags: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Recursive-descent inline parser.

        `flags` accumulate through nesting (`**bold *italic***` -> a run that is
        both). Delimiters require a closing partner: an unmatched `*` stays
        literal rather than italicising the rest of the line.
        """
        flags = flags or {}
        runs: list[dict[str, Any]] = []
        plain = ""
        i = 0
        length = len(text)

        def flush() -> None:
            nonlocal plain
            if plain != "":
                runs.append({"text": plain, **flags})
                plain = ""

        while i < length:
            ch = text[i]

            # Backslash escape -- ANY next character, not just punctuation.
            if ch == "\\" and i + 1 < length:
                plain += text[i + 1]
                i += 2
                continue

            # Code span
            if ch == "`":
                end = text.find("`", i + 1)
                if end != -1 and end > i + 1:
                    flush()
                    runs.append({"text": text[i + 1 : end], **flags, "code": True})
                    i = end + 1
                    continue

            # Bold / italic (asterisk)
            if ch == "*":
                if text[i : i + 2] == "**":
                    end = text.find("**", i + 2)
                    if end != -1 and end > i + 2:
                        flush()
                        runs.extend(self._inline(text[i + 2 : end], {**flags, "bold": True}))
                        i = end + 2
                        continue
                else:
                    end = text.find("*", i + 1)
                    if end != -1 and end > i + 1:
                        flush()
                        runs.extend(self._inline(text[i + 1 : end], {**flags, "italic": True}))
                        i = end + 1
                        continue

            # Italic (underscore)
            # RULING PENDING: documents.md §1.2 rules for Node's word-boundary
            # aware `_`, because this rule mangles `use snake_case_names here`
            # into an italic middle run. The shipped PHP engine has no boundary
            # check and this port follows it. Same ruling makes `__bold__` bold;
            # here it is `_` + italic + `_`.
            if ch == "_":
                end = text.find("_", i + 1)
                if end != -1 and end > i + 1:
                    flush()
                    runs.extend(self._inline(text[i + 1 : end], {**flags, "italic": True}))
                    i = end + 1
                    continue

            # Strikethrough
            if ch == "~" and text[i : i + 2] == "~~":
                end = text.find("~~", i + 2)
                if end != -1 and end > i + 2:
                    flush()
                    runs.extend(self._inline(text[i + 2 : end], {**flags, "strike": True}))
                    i = end + 2
                    continue

            # Link
            if ch == "[":
                match = _LINK_RE.match(text, i)
                if match is not None:
                    flush()
                    runs.extend(
                        self._inline(match.group(1), {**flags, "link": match.group(2)})
                    )
                    i += len(match.group(0))
                    continue

            # Inline image -- the model has no inline-image run; degrade to alt.
            if ch == "!":
                match = _INLINE_IMAGE_RE.match(text, i)
                if match is not None:
                    plain += match.group(1).replace("\\[", "[").replace("\\]", "]")
                    i += len(match.group(0))
                    continue

            plain += ch
            i += 1

        flush()

        return _merge_runs(runs)


# ─── Line classification ─────────────────────────────────────────────────


def _match_list_line(line: str) -> dict[str, Any] | None:
    match = _BULLET_RE.match(line)
    if match is not None:
        return {
            "depth": _indent_depth(match.group(1)),
            "ordered": False,
            "content": php_rtrim(match.group(3)),
        }
    # `1)` is accepted as well as `1.` -- cheap tolerance.
    match = _ORDERED_RE.match(line)
    if match is not None:
        return {
            "depth": _indent_depth(match.group(1)),
            "ordered": True,
            "content": php_rtrim(match.group(3)),
        }
    return None


def _indent_depth(indent: str) -> int:
    """Nesting depth: 2 spaces (or one tab) per level."""
    width = 0
    for char in indent:
        width += 2 if char == "\t" else 1
    return width // 2


def _starts_block(line: str) -> bool:
    return (
        _HEADING_START_RE.match(line) is not None
        or line.startswith("```")
        or _QUOTE_RE.match(line) is not None
        or _HR_RE.match(line) is not None
        or _match_list_line(line) is not None
    )


def _is_table_separator(line: str) -> bool:
    trimmed = php_trim(line)
    if trimmed == "" or "-" not in trimmed:
        return False
    return _SEPARATOR_RE.match(trimmed) is not None and "|" in trimmed


def _split_unescaped_pipes(text: str) -> list[str]:
    """Split on `|` that is not preceded by a backslash.

    A hand-written scanner rather than PHP's `preg_split('/(?<!\\\\)\\|/')`:
    Python's `re` has lookbehind and would transliterate it verbatim, but this
    is the shared behaviour the other ports (Rust's regex crate, Go's RE2) must
    implement WITHOUT lookaround, and a second algorithm is a second chance to
    diverge. Note this matches PHP exactly, including the corner where a literal
    escaped backslash before a pipe (`\\\\|`) also suppresses the split.
    """
    out: list[str] = []
    current: list[str] = []
    for index, char in enumerate(text):
        if char == "|" and (index == 0 or text[index - 1] != "\\"):
            out.append("".join(current))
            current = []
            continue
        current.append(char)
    out.append("".join(current))
    return out


def _merge_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for run in runs:
        if run.get("text", "") == "":
            continue
        if merged:
            a = {k: v for k, v in merged[-1].items() if k != "text"}
            b = {k: v for k, v in run.items() if k != "text"}
            if a == b:
                merged[-1] = {**merged[-1], "text": merged[-1]["text"] + run["text"]}
                continue
        merged.append(run)
    return merged

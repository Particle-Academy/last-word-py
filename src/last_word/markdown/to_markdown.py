"""Doc model -> GFM markdown. The Editor bridge's outbound half.

Hand-rolled (no markdown dependency), emitting the same canonical form
`from_markdown` parses, so the two form a fixpoint:

  headings `#`, emphasis `**` / `*` / `~~`, inline code, links, ordered +
  unordered nested lists (2-space indent per level), tables, fenced code,
  blockquotes, images `![alt](src)`, `---` hr, and an `<!-- pagebreak -->`
  comment so page breaks survive the round-trip.

Lossy by nature of GFM: underline / color / highlight / alignment are dropped;
the doc title is emitted as a leading `#` heading.
"""

from __future__ import annotations

from typing import Any

from ..helpers.php import is_numeric, php_float, php_str, php_truthy

#: Characters backslash-escaped in plain text. Backslash goes FIRST or the
#: escapes added by the later replacements get escaped in turn.
ESCAPE = ("\\", "`", "*", "_", "~", "[", "]")


class ToMarkdown:
    """Mirrors `LastWord\\Markdown\\ToMarkdown`."""

    def convert(self, doc: dict[str, Any]) -> str:
        parts: list[str] = []

        title = doc.get("title")
        if isinstance(title, str) and title != "":
            parts.append("# " + self._escape(title))

        for block in _iter_list(doc.get("blocks")):
            if not isinstance(block, dict):
                continue
            md = self._block(block)
            if md is not None and md != "":
                parts.append(md)

        return "\n\n".join(parts) + "\n"

    # ─── Blocks ──────────────────────────────────────────────────────────

    def _block(self, block: dict[str, Any]) -> str | None:
        # RULING PENDING: documents.md §1.2 rules for Node's `guardLineStart` --
        # a paragraph beginning `- ` or `# ` should be guarded so it does not
        # re-parse as a block. The shipped PHP engine has no guard and this port
        # follows it, so that one input is not a markdown fixpoint here either.
        block_type = block.get("type")
        if block_type == "heading":
            raw = block.get("level")
            level = int(php_float(raw)) if is_numeric(raw) else 1
            return "#" * max(1, min(6, level)) + " " + self._inline(block.get("runs") or [])
        if block_type == "paragraph":
            return self._inline(block.get("runs") or [])
        if block_type == "list":
            return "\n".join(
                self._list(block.get("items") or [], php_truthy(block.get("ordered")), 0)
            )
        if block_type == "table":
            return self._table(block)
        if block_type == "code":
            return self._code(block)
        if block_type == "quote":
            return self._quote(block)
        if block_type == "image":
            return self._image(block)
        if block_type == "pageBreak":
            return "<!-- pagebreak -->"
        if block_type == "hr":
            return "---"
        return None

    def _list(self, items: Any, ordered: bool, depth: int) -> list[str]:
        lines: list[str] = []
        indent = "  " * depth
        n = 1
        for item in _iter_list(items):
            if not isinstance(item, dict):
                continue
            marker = f"{n}." if ordered else "-"
            if ordered:
                n += 1
            lines.append(indent + marker + " " + self._inline(item.get("runs") or []))
            children = item.get("children")
            if php_truthy(children) and isinstance(children, list):
                lines.extend(self._list(children, ordered, depth + 1))
        return lines

    def _table(self, block: dict[str, Any]) -> str:
        rows = [r for r in _iter_list(block.get("rows")) if isinstance(r, dict)]
        if not rows:
            return ""

        def render_row(row: dict[str, Any]) -> str:
            cells = [
                self._cell_text(cell) if isinstance(cell, dict) else ""
                for cell in _iter_list(row.get("cells"))
            ]
            return "| " + " | ".join(cells) + " |"

        # GFM tables require a header row -- the first row serves whether or not
        # it is flagged.
        first_cells = rows[0].get("cells")
        col_count = max(1, len(first_cells) if isinstance(first_cells, list) else 0)
        lines = [render_row(rows[0])]
        lines.append("| " + " | ".join(["---"] * col_count) + " |")
        for row in rows[1:]:
            lines.append(render_row(row))
        return "\n".join(lines)

    def _cell_text(self, cell: dict[str, Any]) -> str:
        parts: list[str] = []
        for block in _iter_list(cell.get("blocks")):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in ("paragraph", "heading"):
                text = self._inline(block.get("runs") or [], True)
            elif block_type == "code":
                text = "`" + php_str(block.get("text", "")).replace("\n", " ").replace(
                    "|", "\\|"
                ) + "`"
            else:
                text = None
            if text is not None and text != "":
                parts.append(text)
        return " ".join(parts)

    @staticmethod
    def _code(block: dict[str, Any]) -> str:
        language = block.get("language", "")
        text = php_str(block.get("text", "")).replace("\r\n", "\n")
        return "```" + (language if isinstance(language, str) else "") + "\n" + text + "\n```"

    def _quote(self, block: dict[str, Any]) -> str:
        inner: list[str] = []
        for child in _iter_list(block.get("blocks")):
            if not isinstance(child, dict):
                continue
            md = self._block(child)
            if md is not None and md != "":
                inner.append(md)
        lines = "\n\n".join(inner).split("\n")
        return "\n".join(">" if line == "" else "> " + line for line in lines)

    @staticmethod
    def _image(block: dict[str, Any]) -> str:
        raw_alt = block.get("alt")
        alt = raw_alt if isinstance(raw_alt, str) else ""
        return (
            "!["
            + alt.replace("[", "\\[").replace("]", "\\]")
            + "]("
            + php_str(block.get("src", ""))
            + ")"
        )

    # ─── Inline ──────────────────────────────────────────────────────────

    def _inline(self, runs: Any, in_table: bool = False) -> str:
        """Render runs as inline markdown, merging adjacent same-format runs so
        we never emit `**a****b**`."""
        merged: list[dict[str, Any]] = []
        for run in _iter_list(runs):
            if not isinstance(run, dict) or not isinstance(run.get("text"), str):
                continue
            key = _format_key(run)
            if merged and merged[-1]["key"] == key:
                merged[-1]["run"] = {
                    **merged[-1]["run"],
                    "text": merged[-1]["run"]["text"] + run["text"],
                }
            else:
                merged.append({"key": key, "run": run})

        return "".join(self._run(entry["run"], in_table) for entry in merged)

    def _run(self, run: dict[str, Any], in_table: bool) -> str:
        # Markdown cannot nest across lines; flatten hard breaks to spaces.
        text = php_str(run.get("text", "")).replace("\r\n", " ").replace("\n", " ")

        if php_truthy(run.get("code")):
            # RULING PENDING: documents.md §1.2 rules for Node's multi-backtick
            # code spans. Replacing an inner backtick with a space is silent
            # data loss, but it is what the shipped PHP engine does.
            s = "`" + text.replace("`", " ") + "`"
        else:
            s = self._escape(text, in_table)
        if php_truthy(run.get("strike")):
            s = "~~" + s + "~~"
        if php_truthy(run.get("italic")):
            s = "*" + s + "*"
        if php_truthy(run.get("bold")):
            s = "**" + s + "**"
        link = run.get("link")
        if isinstance(link, str) and link != "":
            s = "[" + s + "](" + link + ")"
        return s

    @staticmethod
    def _escape(text: str, in_table: bool = False) -> str:
        chars = list(ESCAPE)
        if in_table:
            chars.append("|")
        for char in chars:
            text = text.replace(char, "\\" + char)
        return text


def _format_key(run: dict[str, Any]) -> tuple[Any, ...]:
    return (
        php_truthy(run.get("bold")),
        php_truthy(run.get("italic")),
        php_truthy(run.get("strike")),
        php_truthy(run.get("code")),
        run.get("link"),
    )


def _iter_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []

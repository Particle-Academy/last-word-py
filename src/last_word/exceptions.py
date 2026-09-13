"""Exceptions raised by the public façade."""

from __future__ import annotations

from typing import Any


class SchemaException(RuntimeError):
    """Raised by `to_bytes()` / `write()` when the document cannot be written.

    Carries the structured error list from the Validator so a caller can render
    per-field feedback without re-running validation -- the same contract as
    PHP's `LastWord\\Exceptions\\SchemaException` and Node's `SchemaException`.
    """

    def __init__(self, message: str, errors: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.errors = errors


class UnsupportedFormatException(ValueError):
    """The bytes are a document we recognise and cannot read.

    Mirrors PHP `UnsupportedFormatException` (an `InvalidArgumentException`) and
    Node's. Deliberately distinct from the `RuntimeError` a damaged file raises,
    because the two need different things said to a person: "this file is
    damaged" and "this is a format we do not read, save it as .docx" lead to
    different actions.

    `format` names what was detected, so a host can branch without parsing the
    message: `doc`, `xls`, `ppt`, `msg`, `cfb`, `xlsx`, `pptx`, `ods`, `odp`,
    `unknown`.
    """

    def __init__(self, format: str, message: str) -> None:  # noqa: A002 - the peers' name
        super().__init__(message)
        self.format = format

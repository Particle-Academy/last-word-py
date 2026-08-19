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

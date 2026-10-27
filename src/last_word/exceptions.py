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


class ArchiveRefused(RuntimeError):
    """A source archive refused by the bounded reader, with a constant `reason`.

    Mirrors PHP `LastWord\\Exceptions\\ArchiveRefused` and Node's: the message never
    echoes archive names or document content. Branch on `reason`:
    `archive_limit`, `archive_path`, `archive_duplicate`, `archive_corrupt`,
    `archive_unsupported`, `archive_limits`, `archive_io`. A `RuntimeError`, as a
    damaged file always was here.
    """

    def __init__(self, reason: str) -> None:
        super().__init__("Source archive refused: " + reason)
        self.reason = reason


_TEMPLATE_MESSAGES = {
    "macro_enabled": "Macro-enabled templates (.dotm) are refused: re-save it as a .dotx in Word.",
    "family_mismatch": "This package declares a template of a different kind than its contents.",
    "already_template": "This package is already a template.",
    "already_document": "This package is already a document.",
    "not_convertible": "This package has no single content-type declaration that can be rewritten.",
    "odf_mimetype": "An OpenDocument package must store its mimetype entry first and uncompressed.",
    "bad_target": 'The conversion target must be "document" or "template".',
}


class OfficeTemplateRefused(RuntimeError):
    """An Office or LibreOffice template package refused, with a constant `reason`.

    Mirrors PHP `LastWord\\Exceptions\\OfficeTemplateRefused`: `macro_enabled`,
    `family_mismatch`, `already_template`, `already_document`, `not_convertible`,
    `odf_mimetype`, `bad_target`. Branch on `reason`; the message is for people.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Template refused ({reason}): " + _TEMPLATE_MESSAGES.get(reason, reason))
        self.reason = reason

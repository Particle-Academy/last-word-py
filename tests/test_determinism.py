"""Determinism: the same input always produces the same bytes.

Without this a document can never be a golden fixture, and `fancy-conformance`
declares a determinism flag a precondition for a writer suite at all. It is
also the cheapest possible check that nothing timestamp-shaped leaked into the
output.
"""

from __future__ import annotations

from last_word import to_bytes
from tests.fixtures import DOCS


def test_to_bytes_is_byte_stable() -> None:
    for name, payload in DOCS.items():
        first = to_bytes(payload)
        second = to_bytes(payload)
        assert first == second, f"{name} is not deterministic"


def test_output_is_a_zip_container() -> None:
    for name, payload in DOCS.items():
        assert to_bytes(payload)[:2] == b"PK", f"{name} did not produce a zip"

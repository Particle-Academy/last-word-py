"""`version()` must not be able to lie.

It is a public API method on all six sibling packages, and on **all six it
currently misreports**: every PHP `VERSION` constant is stale against its own
CHANGELOG, every Node `VERSION` module constant is stale against its own
`package.json`, and `@particle-academy/last-word` holds two constants that
contradict each other inside one package (`Agent.version()` says one thing,
`Schema.VERSION` another).

None of that is carelessness. It is the predictable result of a number living in
two files with nothing comparing them — the same failure the envelope's
`kit.json` rule exists to stop, and the same one that let a footer drift twelve
minor versions behind before anyone noticed.

So there is exactly one constant in this package, and this file pins it to the
packaging metadata. It costs one assertion and removes the whole class.
"""

from __future__ import annotations

import re
from pathlib import Path

import last_word

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _declared_version() -> str:
    """The version in `pyproject.toml`, read without a TOML parser.

    `tomllib` is stdlib from 3.11 and would be fine, but a regex keeps this test
    readable and keeps the failure message pointing at the line a human edits.
    """
    for line in PYPROJECT.read_text(encoding="utf-8").splitlines():
        match = re.match(r'^version\s*=\s*"([^"]+)"', line.strip())
        if match:
            return match.group(1)
    raise AssertionError(f"no `version = \"...\"` line in {PYPROJECT}")


def test_the_package_reports_the_version_it_ships_as() -> None:
    assert last_word.version() == _declared_version(), (
        "version() and pyproject.toml disagree. Both peers have exactly this defect "
        "in production -- fix the constant, do not relax this test."
    )


def test_dunder_version_agrees_too() -> None:
    """`__version__` is what tooling reads; `version()` is what an agent calls."""
    assert last_word.__version__ == last_word.version()


def test_the_version_is_a_semver_triple() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", last_word.version())

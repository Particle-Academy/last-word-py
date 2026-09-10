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

import pytest

import last_word

DISTRIBUTION = "fancy-last-word"
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
    if not _is_installed():
        pytest.skip(
            f"{DISTRIBUTION} is not installed here, so `version()` is the deliberate "
            "uninstalled-tree fallback rather than a real version. CI installs the "
            "package (`pip install -e .`) before running these, so this DOES run "
            "there — it is skipped here, not passing here."
        )

    assert last_word.version() == _declared_version(), (
        "version() and pyproject.toml disagree. Both peers have exactly this defect "
        "in production -- fix the constant, do not relax this test."
    )


def test_dunder_version_agrees_too() -> None:
    """`__version__` is what tooling reads; `version()` is what an agent calls."""
    if not _is_installed():
        pytest.skip(
            f"{DISTRIBUTION} is not installed here, so `version()` is the deliberate "
            "uninstalled-tree fallback rather than a real version. CI installs the "
            "package (`pip install -e .`) before running these, so this DOES run "
            "there — it is skipped here, not passing here."
        )

    assert last_word.__version__ == last_word.version()


def test_the_version_is_a_semver_triple() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", last_word.version())


def _is_installed() -> bool:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as distribution_version

    try:
        distribution_version(DISTRIBUTION)
    except PackageNotFoundError:
        return False
    return True


def test_the_version_is_not_a_hardcoded_literal() -> None:
    """Guard the FIX, not just its result — and do it without an install.

    The three assertions above compare VALUES, so they need a distribution to
    compare against; a bare source tree has none, and they skip. That makes this
    the one that actually holds the line in a working tree, because what it
    guards is someone typing the literal back in — which is exactly how this bug
    happened the first time, and how it happened AGAIN in `fancy-flow-py` (a
    literal `"0.1.0"` against a 0.4.0 distribution for three releases, caught by
    the runtime's first outside consumer rather than by us).

    Deliberately not a regex: the pattern needs both quote characters inside a
    character class, which is three escaping layers deep and was written wrong
    twice while this was being added. A string comparison has no escaping layer
    to get wrong.
    """
    module = Path(last_word.__file__)

    for source_file in (module, module.parent / "agent.py"):
        if not source_file.exists():
            continue

        for line in source_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("__version__ =", "VERSION =")):
                continue

            value = stripped.split("=", 1)[1].strip()
            assert not value.startswith(('"', "'")), (
                f"`{stripped}` in {source_file.name} assigns a string literal. Read "
                "the version from the installed distribution metadata instead — a "
                "literal is a second copy of pyproject.toml's number, and second "
                "copies drift silently."
            )

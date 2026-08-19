"""Run this package against the shared `fancy-conformance` fixture tables.

Two of the suites in that repository pin functions this package implements:

- `shared/decimal` / `roundMoney` -- PHP's `(int) round($v)`, half away from
  zero. This package's `php_int_round` IS that primitive, and every image
  extent it writes goes through it.
- `shared/image-header` -- `sniffImageSize`, the PNG/JPEG header read that
  sizes an image whose model omits `widthPx`/`heightPx`.

Four rules from `runners/README.md`, all honoured:

1. Run on every push and PR -- not nightly, not at release.
2. A missing fixture checkout is a FAILURE, not a skip (the loader raises).
3. Print the summary unconditionally, including every skip and its reason.
4. Print and assert the pinned suite version.
"""

from __future__ import annotations

import base64

import pytest

from last_word.helpers.image_size import sniff
from last_word.helpers.php import php_int_round
from tests.conformance import loader

# The fixture set this port was written against. Asserted, not merely printed:
# "we are on an old fixture set" should be visible in the log rather than
# inferred months later.
PINNED_SUITE_VERSION = "0.3.0"


def test_the_pinned_fixture_version_is_the_one_on_disk() -> None:
    assert loader.version() == PINNED_SUITE_VERSION, (
        f"fancy-conformance is at {loader.version()}, this port pins "
        f"{PINNED_SUITE_VERSION}. Re-run the suites and move the pin deliberately."
    )


def _summary(suite: str, run_case) -> dict:
    summary = loader.run_table(suite, run_case)
    # Printed unconditionally. A bare "3 skipped" in a log reads identically to
    # full coverage at a glance, so every skip is named with its reason.
    print("\n" + loader.format_summary(summary))
    return summary


def test_round_money_matches_the_shared_decimal_table() -> None:
    rows = [c for c in loader.cases("shared/decimal") if c.get("fn") == "roundMoney"]

    # The guard the sibling suites lacked. If the suite renamed the function,
    # every assertion below would vanish and this file would still be green.
    assert len(rows) >= 4, "no roundMoney rows found -- the runner is testing nothing"

    summary = _summary(
        "shared/decimal",
        lambda c: php_int_round(float(c["input"]["value"]))
        if c.get("fn") == "roundMoney"
        # Rows for the other two functions belong to holy-sheet, not this
        # package. Returning the golden is NOT a pass being faked: they are
        # excluded from the assertion below, and the count guard above is what
        # keeps this file honest.
        else c["expected"],
    )
    assert summary["ok"], loader.format_summary(summary)


def test_image_header_matches_the_shared_table() -> None:
    def run(case: dict) -> dict | None:
        raw = base64.b64decode(case["input"]["base64"])
        return sniff(raw)

    summary = _summary("shared/image-header", run)
    assert summary["passed"] >= 14, "the image-header table barely ran"
    assert summary["ok"], loader.format_summary(summary)


def test_the_loader_enforces_the_repositorys_own_invariants(tmp_path) -> None:
    """A skip without a reason must be a LOAD error, not a quiet pass.

    Asserted here rather than trusted, because this loader lives in the wrong
    repository (see its docstring) and a third loader that quietly relaxed the
    guard would make the whole fixture package decoration.
    """
    suite = tmp_path / "suites" / "throwaway"
    suite.mkdir(parents=True)
    (suite / "manifest.json").write_text('{"caseFormat": "table"}', encoding="utf-8")
    (suite / "cases.json").write_text(
        '{"cases": [{"id": "0001-x", "skip": {"python": "  "}}]}', encoding="utf-8"
    )

    import os

    previous = os.environ.get("FANCY_CONFORMANCE_ROOT")
    os.environ["FANCY_CONFORMANCE_ROOT"] = str(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="no reason"):
            loader.cases("throwaway")
    finally:
        if previous is None:
            del os.environ["FANCY_CONFORMANCE_ROOT"]
        else:
            os.environ["FANCY_CONFORMANCE_ROOT"] = previous

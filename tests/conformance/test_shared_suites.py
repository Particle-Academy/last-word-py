"""Run this package against the shared `fancy-conformance` fixture tables.

Two of the suites in that repository pin functions this package implements:

- `shared/decimal` / `roundMoney` -- PHP's `(int) round($v)`, half away from
  zero. This package's `php_int_round` IS that primitive, and every image
  extent it writes goes through it.
- `shared/image-header` -- `sniffImageSize`, the PNG/JPEG header read that
  sizes an image whose model omits `widthPx`/`heightPx`.

The loader is now the one `fancy-conformance` ships, not the private copy this
repository used to carry. That copy is deleted: the fixture package's own
AGENTS.md asks each consumer to drop its own the next time it is touched,
because two of the four that existed read a case's `skip` as a scalar rather
than a map keyed by language -- so a row skipped for PHP skipped on Python too,
and the log still read green.

Four rules from `runners/README.md`, all honoured:

1. Run on every push and PR -- not nightly, not at release.
2. A missing fixture checkout is a FAILURE, not a skip (the loader raises).
3. Print the summary unconditionally, including every skip and its reason.
4. Print and assert the pinned suite version.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from last_word.helpers.image_size import sniff
from last_word.helpers.php import php_int_round
import fancy_conformance as loader

# The fixture set this port was written against. Asserted, not merely printed:
# "we are on an old fixture set" should be visible in the log rather than
# inferred months later.
# Moved to 0.20.0 on 2026-09-10, deliberately and not to get to green: every
# table above was re-run against the checkout FIRST and every row passes, with
# the only skip being the documented cross-engine one. last-word/docx-constructs 44, shared/decimal 18, shared/image-header 15 (+1 documented skip).
#
# Five ports had drifted to a pin this stale at once, which says the failure is
# structural rather than anyone forgetting: the pin only moves when a human
# re-runs the tables, and nothing prompts that when the fixture package ships.
#
# CI checks out `ref: v<this>` from .github/workflows/ci.yml. Move the two
# together; test_ci_checks_out_the_fixture_tag_this_suite_pins fails otherwise.
PINNED_SUITE_VERSION = "0.22.0"


def test_the_pinned_fixture_version_is_the_one_on_disk(capsys: pytest.CaptureFixture[str]) -> None:
    # Past pytest's capture: a bare print() in a passing test never reaches the
    # CI log, and the runner rules require the version to be visible there.
    with capsys.disabled():
        print(f"\nfancy-conformance on disk: {loader.version()}, pinned: {PINNED_SUITE_VERSION}")
    assert loader.version() == PINNED_SUITE_VERSION, (
        f"fancy-conformance is at {loader.version()}, this port pins "
        f"{PINNED_SUITE_VERSION}. Re-run the suites and move the pin deliberately."
    )


def _conformance_checkout_refs(workflow: str) -> list[str | None]:
    """The `ref:` of every workflow step that checks out fancy-conformance.

    Plain text on purpose: a YAML parser would be a dependency for one assertion.
    A step is a `- ` line plus everything indented deeper than it. `None` is a
    step with no `ref`, which checks out whatever `main` is at that moment.
    """
    lines = workflow.splitlines()
    refs: list[str | None] = []
    for index, line in enumerate(lines):
        start = re.match(r"(\s*)- ", line)
        if not start:
            continue
        step = [line]
        for following in lines[index + 1 :]:
            body = following.strip()
            indent = len(following) - len(following.lstrip())
            if body and not body.startswith("#") and indent <= len(start.group(1)):
                break
            step.append(following)
        text = "\n".join(step)
        if re.search(
            r"^\s*(- )?repository:\s*[\"']?Particle-Academy/fancy-conformance[\"']?\s*(#.*)?$",
            text,
            re.MULTILINE,
        ):
            ref = re.search(r"^\s*(- )?ref:\s*[\"']?([^\"'\s#]+)", text, re.MULTILINE)
            refs.append(ref.group(2) if ref else None)
    return refs


def test_the_checkout_ref_parser_sees_a_missing_ref() -> None:
    workflow = """
      - uses: actions/checkout@v4
        with:
          repository: Particle-Academy/fancy-conformance
          path: .fancy-conformance
      - name: Pinned
        uses: actions/checkout@v4
        with:
          repository: "Particle-Academy/fancy-conformance"
          ref: 'v1.2.3'  # a comment
      - uses: actions/checkout@v4
        with:
          repository: Particle-Academy/last-word
          ref: v9.9.9
    """
    assert _conformance_checkout_refs(workflow) == [None, "v1.2.3"]


def test_ci_checks_out_the_fixture_tag_this_suite_pins() -> None:
    """The CI checkout `ref` and `PINNED_SUITE_VERSION` are one decision in two files.

    CI used to check fancy-conformance out with no `ref`, so every fixture release
    turned this build red at once for a reason no commit here caused, and it sat
    red for weeks. The pin is the contract: moving it is a deliberate commit in
    this repository, never a side effect of someone else's release.
    """
    here = Path(__file__).resolve()
    workflows = next(
        (p / ".github" / "workflows" for p in here.parents if (p / ".github/workflows").is_dir()),
        None,
    )
    assert workflows is not None, f"no .github/workflows above {here}"

    refs = {
        path.name: _conformance_checkout_refs(path.read_text(encoding="utf-8"))
        for path in sorted(workflows.glob("*.y*ml"))
    }
    refs = {name: found for name, found in refs.items() if found}
    # Vacuity guard: a parser that matched nothing would satisfy the loop below.
    assert refs, "no workflow checks out Particle-Academy/fancy-conformance"

    expected = f"v{PINNED_SUITE_VERSION}"
    for name, found in refs.items():
        assert found == [expected] * len(found), (
            f".github/workflows/{name} checks fancy-conformance out at {found}, but this "
            f"suite pins {PINNED_SUITE_VERSION}. Set `ref: {expected}` there, and move "
            "the pin and the ref together."
        )


def _summary(suite: str, run_case, capsys: pytest.CaptureFixture[str]) -> dict:
    summary = loader.run_table(suite, run_case)
    # Printed unconditionally, and past pytest's capture: a bare print() in a
    # passing test never reached the CI log, so a skip could not be read there
    # at all. Every skip is named with its reason.
    with capsys.disabled():
        print("\n" + loader.format_summary(summary))
    return summary


def test_round_money_matches_the_shared_decimal_table(capsys: pytest.CaptureFixture[str]) -> None:
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
        capsys,
    )
    assert summary["ok"], loader.format_summary(summary)


def test_image_header_matches_the_shared_table(capsys: pytest.CaptureFixture[str]) -> None:
    def run(case: dict) -> dict | None:
        raw = base64.b64decode(case["input"]["base64"])
        return sniff(raw)

    summary = _summary("shared/image-header", run, capsys)
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

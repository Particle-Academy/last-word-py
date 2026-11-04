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
# Moved 0.22.0 -> 0.22.1 on 2026-09-13. That release changed no case and no
# golden (the Rust loader pins fancy-json by tag, plus docs); re-run first against
# a v0.22.1 checkout all the same: last-word/docx-constructs 44, shared/decimal
# 18, shared/image-header 15 (+1 documented skip, 0012), nothing failed, the same
# counts CI printed at 0.22.0.
#
# Moved 0.22.1 -> 0.34.0 on 2026-10-19, re-run first against a checkout at the
# v0.34.0 tag: last-word/docx-constructs 44, shared/decimal 18, shared/image-header
# 15 (+1 documented skip, 0012), nothing failed -- the same counts as at 0.22.1.
# It had blocked every local release preflight since the sibling checkout moved.
#
# Moved 0.34.0 -> 0.40.0 on 2026-11-01 with the footnote and page-geometry port
# (PHP last-word 0.15.0): last-word/docx-constructs gained rows 0045-0053, which
# need the new `footnotesPart` extractor, and doc-model/shared 0006 records
# `footnote` as a last-word-only run property (checked below, last-word half).
# Re-run against a checkout at the v0.40.0 tag first.
#
# Moved 0.40.0 -> 0.41.0 on 2026-11-01 with the first/even band, field token and
# meta port (PHP last-word 0.16.0): last-word/docx-constructs gained rows
# 0054-0079 and the bandParts / bandFields / coreProps extractors, and
# doc-model/shared 0001 / 0002 record `meta` and the four band variants as
# last-word-only. Re-run against a checkout at the v0.41.0 tag first.
#
# Moved 0.41.0 -> 0.44.0 on 2026-11-03 with the mixed page refusals: 0.44.0 adds
# doc-model/page-refusal and doc-model/shared 0015-page-break, and fine-print now
# draws what 0001 / 0002 / 0006 recorded as last-word-only. Run at the v0.44.0 tag.
#
# CI checks out `ref: v<this>` from .github/workflows/ci.yml. Move the two
# together; test_ci_checks_out_the_fixture_tag_this_suite_pins fails otherwise.
PINNED_SUITE_VERSION = "0.44.0"


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


# doc-model/shared rows this port is KNOWN to fail, with why. Both are PHP
# last-word 0.12.0's page decoration (owner decision 11: `page.background` and
# `page.border`), which this port has not been brought up to. A ratchet both
# ways: a new failure fails, and a row listed here that starts passing fails
# too, so the list cannot rot into a permanent excuse.
DOC_MODEL_KNOWN_GAPS: dict[str, str] = {
    # Since 0.41.0 this row also lists the four band variants, which this port
    # has; it still fails on background / border alone.
    "0002-page": "no page.background / page.border in this port yet (PHP 0.12.0)",
    "0003-page-border": "no page.border in this port yet (PHP 0.12.0)",
}


def _lw_schema_def(schema: dict, name: str) -> dict:
    """The last-word schema node for a doc-model definition (PHP `lwxSchemaDef`)."""
    blocks = {b["properties"]["type"]["const"]: b for b in schema["$defs"]["block"]["oneOf"]}
    table = blocks["table"]["properties"]
    nodes = {
        "document": schema,
        "page": schema["properties"]["page"],
        "pageBorder": schema["properties"]["page"]["properties"].get("border", {"properties": {}}),
        "band": schema["$defs"]["band"],
        "pageMargins": schema["$defs"]["pageMargins"],
        "run": schema["$defs"]["run"],
        "paragraph": blocks["paragraph"],
        "heading": blocks["heading"],
        "table": blocks["table"],
        "image": blocks["image"],
        "pageBreak": blocks["pageBreak"],
        "listItem": schema["$defs"]["listItem"],
        "tableRow": table["rows"]["items"],
        "tableCell": table["rows"]["items"]["properties"]["cells"]["items"],
        "border": schema["$defs"]["border"],
    }
    return nodes[name]


def _lw_constraint(schema: dict, path: str) -> dict:
    """The keywords last-word declares at a property (PHP `lwxLastWordConstraint`)."""
    nodes = {
        "run.link": schema["$defs"]["run"]["properties"]["link"],
        "run.size": schema["$defs"]["run"]["properties"]["size"],
        "defaultSize": schema["properties"]["defaultSize"],
        "band.size": schema["$defs"]["band"]["properties"]["size"],
        "image.src": _lw_schema_def(schema, "image")["properties"]["src"],
        "page.size": schema["properties"]["page"]["properties"]["size"],
    }
    keep = ("type", "pattern", "enum", "minimum", "maximum", "exclusiveMinimum")
    return {k: v for k, v in nodes[path].items() if k in keep}


def test_satisfies_the_last_word_half_of_doc_model_shared(capsys: pytest.CaptureFixture[str]) -> None:
    """The last-word half of `doc-model/shared`, as PHP last-word runs it.

    The suite compares last-word's schema with fine-print's and names fine-print
    PHP as its runner; each package runs ITS half: `compare` -> last-word's
    property set is exactly `both` + `lastWordOnly`; `constraint` -> its
    keywords are exactly `lastWord`. Row 0006 (since 0.40.0) lists `footnote`
    in last-word's half of the run properties.
    """
    from last_word import json_schema

    schema = json_schema()
    cases = loader.cases("doc-model/shared")
    failed: dict[str, str] = {}
    for case in cases:
        name = case["input"]["def"]
        if case["fn"] == "compare":
            want = sorted([*case["expected"]["both"], *case["expected"]["lastWordOnly"]])
            if name == "blockTypes":
                got = sorted(b["properties"]["type"]["const"] for b in schema["$defs"]["block"]["oneOf"])
            else:
                got = sorted(_lw_schema_def(schema, name)["properties"])
        else:
            want, got = case["expected"]["lastWord"], _lw_constraint(schema, name)
        if not loader.equals(got, want):
            failed[case["id"]] = f"expected {want} got {got}"

    with capsys.disabled():
        print()
        print(
            f"doc-model/shared [python, last-word half] - fancy-conformance {loader.version()}: "
            f"{len(cases) - len(failed)} passed, {len(failed)} failed (known: {sorted(DOC_MODEL_KNOWN_GAPS)}) of {len(cases)}"
        )

    # 0.41.0: meta and the band variants are in last-word's half (0.44.0 moved them
    # and footnote from last-word-only to both, as fine-print gained them), and
    # this port has every one of them, so the last-word half of 0002 differs
    # from this schema by background / border and nothing else.
    by_id = {c["id"]: c for c in cases}
    doc_row = by_id["0001-document"]["expected"]
    assert "meta" in {*doc_row["both"], *doc_row["lastWordOnly"]}
    page_props = set(schema["properties"]["page"]["properties"])
    page_want = {*by_id["0002-page"]["expected"]["both"], *by_id["0002-page"]["expected"]["lastWordOnly"]}
    assert page_want - page_props == {"background", "border"}, page_want - page_props
    assert page_props <= page_want

    run_rows = [c for c in cases if c["input"]["def"] == "run"]
    run_row = run_rows[0]["expected"]
    assert len(run_rows) == 1 and "footnote" in {*run_row["both"], *run_row["lastWordOnly"]}
    assert len(cases) >= 20, "doc-model/shared barely loaded"
    assert sorted(failed) == sorted(DOC_MODEL_KNOWN_GAPS), failed


def test_satisfies_the_last_word_half_of_page_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    """The last-word half of `doc-model/page-refusal`: the same messages as PHP.

    Messages only: paths differ by runtime, and the suite says so.
    """
    from last_word import validate, validate_and_repair

    cases = loader.cases("doc-model/page-refusal")
    failed: dict[str, str] = {}
    for case in cases:
        doc, want = case["input"]["doc"], case["expected"]["lastWord"]
        repaired = validate_and_repair(doc)
        got: dict = {"repairOk": repaired["ok"]}
        if "pageKept" in want:
            kept = lambda blocks: sum(1 for b in blocks if b.get("type") == "pageBreak" and "page" in b)  # noqa: E731
            got["pageKept"] = kept(repaired["schema"]["blocks"]) == kept(doc["blocks"])
        if "messages" in want:
            got["messages"] = [e["message"] for e in validate(doc)]
        if not loader.equals(got, want):
            failed[case["id"]] = f"expected {want} got {got}"

    with capsys.disabled():
        print()
        print(
            f"doc-model/page-refusal [python, last-word half] - fancy-conformance {loader.version()}: "
            f"{len(cases) - len(failed)} passed, {len(failed)} failed of {len(cases)}"
        )

    assert len(cases) >= 18, "doc-model/page-refusal barely loaded"
    assert failed == {}, failed

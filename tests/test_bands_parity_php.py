"""First/even bands, field tokens and `meta.author`, held to the PHP writer.

Two kinds of evidence, both about bytes:

1. **Nothing that existed before writes differently.** The digests below are
   PHP's own `UnchangedOutputTest` goldens -- produced by running released PHP
   last-word 0.15.0 (4172ed5) -- and this port's released 0.8.0 (734154f) was
   run over the same documents BEFORE any of this was written and produced the
   identical digests. So one number pins both engines' old output.
2. **What is new is PHP's, part for part.** Every document in PARITY_DOCS is
   written by both writers and every part compared byte for byte.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from last_word import to_bytes

BODY = [{"type": "paragraph", "runs": [{"text": "Body text, with {date} and {author} left literal in the body."}]}]

UNCHANGED_CASES: dict[str, dict[str, Any]] = {
    "slot bands with page fields": {
        "title": "Board memo",
        "page": {
            "header": {"left": "Q3 | Confidential", "right": "{Title} {titles} {date:} {foo}"},
            "footer": {"left": "Acme", "right": "Page {page} of {pages}"},
        },
        "blocks": BODY,
    },
    "title only": {"title": "Only a title", "blocks": BODY},
}

UNCHANGED_DIGESTS = {
    # PHP UnchangedOutputTest's goldens; this port's 0.8.0 produced the same.
    "slot bands with page fields": "20a846e21564e495a47962fa25c56a5053e3115c416585aef79e86819ad0416b",
    "title only": "987a45b31a5c49fb1f4006417e10689f5f6edd0de277b054b60500fbc303a047",
    # This port's own: tests/data/canonical.json written by 0.8.0.
    "canonical fixture": "1d8046d34b80d011bff0fc2b1d56f6145a48d3f723fc1ff185ddd35598dfc58b",
}


def digest(data: bytes) -> str:
    """Part names in archive order, each with the SHA-256 of its content, hashed together."""
    lines = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            lines.append(name + " " + hashlib.sha256(archive.read(name)).hexdigest())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _unchanged_doc(name: str) -> dict[str, Any]:
    if name == "canonical fixture":
        path = Path(__file__).resolve().parent / "data" / "canonical.json"
        return json.loads(path.read_text(encoding="utf-8"))
    return UNCHANGED_CASES[name]


@pytest.mark.parametrize("name", sorted(UNCHANGED_DIGESTS))
def test_writes_what_0_8_0_wrote(name: str) -> None:
    assert digest(to_bytes(_unchanged_doc(name))) == UNCHANGED_DIGESTS[name]


LETTER = [
    {"type": "paragraph", "runs": [{"text": "Dear customer,"}]},
    {"type": "paragraph", "runs": [{"text": "A letter of several pages."}]},
]

PARITY_DOCS: dict[str, dict[str, Any]] = {
    "firstHeader": {
        "page": {
            "header": {"left": "Every later page"},
            "firstHeader": {"left": "The first page", "right": "Acme Ltd"},
            "footer": {"center": "{page}"},
        },
        "blocks": LETTER,
    },
    "firstOnly": {"page": {"firstHeader": {"left": "Only on page one"}}, "blocks": LETTER},
    "evenBands": {
        "page": {
            "header": {"right": "Odd"},
            "evenHeader": {"left": "Even", "size": 8},
            "footer": {"center": "{page}"},
            "evenFooter": {"left": "{page}", "color": "#334455"},
        },
        "blocks": LETTER,
    },
    "allSix": {
        "title": "Board memo",
        "page": {
            "margins": {"left": 54, "right": 54},
            "header": {"left": "Odd"},
            "footer": {"center": "{page}"},
            "firstHeader": {"left": "First"},
            "firstFooter": {"center": "Cover"},
            "evenHeader": {"left": "Even"},
            "evenFooter": {"center": "Even {page}"},
        },
        "blocks": LETTER,
    },
    "blankFirstBlocks": {"page": {"header": {"left": "Not on the cover"}, "firstHeader": {"blocks": []}}, "blocks": LETTER},
    "blankFirstNoText": {"page": {"footer": {"left": "Acme"}, "firstFooter": {}, "evenFooter": {"left": ""}}, "blocks": LETTER},
    "emptyVariantsAlone": {"page": {"firstHeader": {"blocks": []}, "firstFooter": []}, "blocks": LETTER},
    "evenWithFootnote": {
        "page": {"header": {"left": "Odd"}, "evenHeader": {"left": "Even"}},
        "blocks": [
            {
                "type": "paragraph",
                "runs": [
                    {"text": "Noted"},
                    {"text": "", "footnote": {"blocks": [{"type": "paragraph", "runs": [{"text": "n"}]}]}},
                ],
            }
        ],
    },
    "everyToken": {
        "title": "Board memo",
        "meta": {"author": "Ada Lovelace"},
        "page": {
            "header": {"left": "Printed {date} at {time}", "right": "{filename}"},
            "footer": {
                "left": "{author} | {title}",
                "center": "Section {section} of {sectionpages}",
                "right": "Page {page} of {pages}",
            },
        },
        "blocks": LETTER,
    },
    "tokenFormats": {
        "page": {"footer": {"left": "{date:yyyy-MM-dd}", "center": "{date:d MMMM yyyy}", "right": "{time:HH:mm}"}},
        "blocks": LETTER,
    },
    "notTokens": {
        "page": {"footer": {"left": '{Date} {foo} {date:} {page:x} {author:x} {date:a"b} {ti<tle}'}},
        "blocks": LETTER,
    },
    "fieldsWithoutMeta": {"page": {"footer": {"left": "{author} | {title}"}}, "blocks": LETTER},
    "authorNeedsEscaping": {
        "title": "R&D <memo>",
        "meta": {"author": " Ada & \"Co\" "},
        "page": {"firstFooter": {"left": "{author}/{title}"}},
        "blocks": LETTER,
    },
    "authorOnly": {"meta": {"author": "Ada Lovelace"}, "blocks": LETTER},
    "emptyAuthorAndTitle": {"title": "", "meta": {"author": ""}, "blocks": LETTER},
    "emptyMeta": {"meta": {}, "blocks": LETTER},
}


@pytest.mark.parity
@pytest.mark.parametrize("name", sorted(PARITY_DOCS))
def test_emits_the_same_parts_as_php(php_oracle, name: str) -> None:
    payload = PARITY_DOCS[name]
    php_parts = php_oracle.parts(php_oracle.php_to_bytes(payload))
    py_parts = php_oracle.parts(to_bytes(payload))

    assert sorted(py_parts) == sorted(php_parts)
    for part in sorted(php_parts):
        assert py_parts[part] == php_parts[part], f"{part} diverges from PHP for {name!r}"


def test_the_parity_documents_reach_what_they_claim() -> None:
    def parts(name: str) -> dict[str, str]:
        with zipfile.ZipFile(io.BytesIO(to_bytes(PARITY_DOCS[name]))) as archive:
            return {n: archive.read(n).decode("utf-8") for n in archive.namelist()}

    six = parts("allSix")
    assert {f"word/{k}{n}.xml" for k in ("header", "footer") for n in (1, 2, 3)} <= set(six)
    assert "<w:titlePg/>" in six["word/document.xml"]
    assert "<w:evenAndOddHeaders/>" in six["word/settings.xml"]
    assert "<w:dc:creator" not in parts("everyToken")["docProps/core.xml"]
    assert "<dc:creator>Ada Lovelace</dc:creator>" in parts("everyToken")["docProps/core.xml"]
    assert " SECTIONPAGES " in parts("everyToken")["word/footer1.xml"]
    assert 'DATE \\@ &quot;yyyy-MM-dd&quot;' in parts("tokenFormats")["word/footer1.xml"]
    assert "fldChar" not in parts("notTokens")["word/footer1.xml"]
    assert "<w:p/>" in parts("blankFirstNoText")["word/footer2.xml"]
    assert "word/header2.xml" not in parts("emptyVariantsAlone")
    assert "docProps/core.xml" not in parts("emptyAuthorAndTitle") or "dc:creator" not in parts("emptyAuthorAndTitle")["docProps/core.xml"]

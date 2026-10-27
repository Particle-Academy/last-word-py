"""Template conversion, held to PHP's bytes: the WHOLE output archive.

The converter writes the archive itself (raw records copied, one member
re-emitted), so unlike the writer -- whose container differs by design -- its
output container is expected to be byte-identical across engines. A refusal is
compared by reason code.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

import last_word
from last_word import ArchiveRefused, OfficeTemplateRefused
from tests import _oracle
from tests.test_office_template import MACRO_TEMPLATE, ODT, OTT, PPT_TEMPLATE, docx, dotx, odf, retyped

SCRIPT = _oracle.REPO_ROOT / "scripts" / "php_convert_template.php"


def php_convert(data: bytes, to: str | None) -> tuple[bytes | None, str | None]:
    with tempfile.TemporaryDirectory(prefix="last-word-template-") as tmp:
        source, out = Path(tmp) / "in.bin", Path(tmp) / "out.bin"
        source.write_bytes(data)
        env = {**os.environ, "LAST_WORD_PHP_SRC": str(_oracle.php_src_root())}
        args = [_oracle.php_binary() or "php", str(SCRIPT), str(source), str(out)] + ([to] if to else [])
        run = subprocess.run(args, capture_output=True, env=env, check=False)
        if run.returncode == 4:
            return None, run.stderr.decode()
        assert run.returncode == 0, run.stderr.decode("utf-8", "replace")[:2000]
        return out.read_bytes(), None


def python_convert(data: bytes, to: str | None) -> tuple[bytes | None, str | None]:
    try:
        return last_word.convert_template(data, to), None
    except (OfficeTemplateRefused, ArchiveRefused) as error:
        return None, error.reason


CASES = {
    "dotx to docx": (dotx, None),
    "docx to dotx": (docx, "template"),
    "odt to ott": (lambda: odf(ODT), None),
    "ott to odt": (lambda: odf(OTT), "document"),
    "already a template": (dotx, "template"),
    "macro template": (lambda: retyped(MACRO_TEMPLATE), None),
    "another family": (lambda: retyped(PPT_TEMPLATE), None),
}


@pytest.mark.parametrize("case", list(CASES))
def test_conversion_matches_php_byte_for_byte(php_oracle: object, case: str) -> None:
    build, to = CASES[case]
    data = build()
    assert python_convert(data, to) == php_convert(data, to)

"""Official template formats: Word .dotx and LibreOffice .ott.

Mirrors PHP `tests/Unit/OfficeTemplateTest.php` case for case.

A template IS its document twin with a different declared type: the main part's
content type in [Content_Types].xml (OOXML), or the `mimetype` entry plus the
manifest's root entry (ODF 1.3 Part 2 section 3.3 requires the two to be equal).
The reader marks `sourceKind: "template"`; the converter flips the declaration
and copies every other part byte for byte.

Every fixture here is built by this file with `zipfile`, independently of the
production converter, so a converter bug cannot also forge its own evidence.
"""

from __future__ import annotations

import io
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

import pytest

import last_word
from last_word import ArchiveRefused, OfficeTemplateRefused, UnsupportedFormatException
from last_word.reader.odt_reader import OdtReader
from tests.fixtures import RED_PNG_DATA_URL

DOC = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
TEMPLATE = "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"
MACRO_TEMPLATE = "application/vnd.ms-word.template.macroEnabledTemplate.main+xml"
MACRO_DOC = "application/vnd.ms-word.document.macroEnabled.main+xml"
PPT_TEMPLATE = "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml"
ODT = "application/vnd.oasis.opendocument.text"
OTT = "application/vnd.oasis.opendocument.text-template"

REPO = Path(__file__).resolve().parent.parent


def a_doc() -> dict[str, Any]:
    return {"title": "Letterhead", "blocks": [
        {"type": "heading", "level": 1, "runs": [{"text": "Dear reader"}]},
        {"type": "paragraph", "runs": [{"text": "Body text, "}, {"text": "bold", "bold": True}]},
        {"type": "image", "src": RED_PNG_DATA_URL, "widthPx": 2, "heightPx": 2},
    ]}


def entries(data: bytes) -> dict[str, bytes]:
    """Entry name -> uncompressed bytes, in archive order."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def methods(data: bytes) -> dict[str, int]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: info.compress_type for info in archive.infolist()}


def rezip(parts: dict[str, bytes | str]) -> bytes:
    """Re-zip in order; `mimetype` STORED as ODF requires, everything else deflated."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in parts.items():
            method = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            archive.writestr(name, data, compress_type=method)
    return buffer.getvalue()


def docx() -> bytes:
    return last_word.to_bytes(a_doc())


def retyped(content_type: str, extra: dict[str, bytes | str] | None = None) -> bytes:
    """The docx with its main part re-declared; nothing else differs."""
    parts: dict[str, bytes | str] = dict(entries(docx()))
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(DOC.encode(), content_type.encode())  # type: ignore[union-attr]
    return rezip({**parts, **(extra or {})})


def dotx() -> bytes:
    return retyped(TEMPLATE)


def manifest(media_type: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">\n'
        ' <manifest:file-entry manifest:full-path="/" manifest:version="1.3" manifest:media-type="' + media_type + '"/>\n'
        ' <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>\n'
        "</manifest:manifest>"
    )


def odf(mimetype: str, body: str = "text", manifest_type: str | None = None) -> bytes:
    return rezip({
        "mimetype": mimetype,
        "content.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
            ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
            f'<office:body><office:{body}><text:h text:outline-level="1">A Heading</text:h><text:p>Hello from ODF</text:p></office:{body}></office:body>'
            "</office:document-content>"
        ),
        "meta.xml": '<?xml version="1.0" encoding="UTF-8"?><office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta><dc:title>Memo</dc:title></office:meta></office:document-meta>',
        "Pictures/opaque.bin": b"\x00\x01\x02binary\xff",
        "META-INF/manifest.xml": manifest(manifest_type or mimetype),
    })


def refused(action: Callable[[], Any], reason: str) -> None:
    with pytest.raises(OfficeTemplateRefused) as caught:
        action()
    assert caught.value.reason == reason


def archive_refused(action: Callable[[], Any], reason: str) -> None:
    with pytest.raises(ArchiveRefused) as caught:
        action()
    assert caught.value.reason == reason


# ─── Reading ─────────────────────────────────────────────────────────────────


def test_reads_a_word_dotx_and_marks_it_as_a_template() -> None:
    document = last_word.read(docx())
    template = last_word.read(dotx())
    assert template.get("sourceKind") == "template"
    del template["sourceKind"]
    assert template == document


def test_leaves_a_docx_read_exactly_as_it_was() -> None:
    assert "sourceKind" not in last_word.read(docx())


def test_reads_a_libreoffice_ott_and_marks_it_as_a_template() -> None:
    document = last_word.read(odf(ODT))
    template = last_word.read(odf(OTT))
    assert "sourceKind" not in document
    assert template.get("sourceKind") == "template"
    del template["sourceKind"]
    assert template == document


def test_sourcekind_is_the_last_key_as_in_php() -> None:
    assert list(last_word.read(dotx()))[-1] == "sourceKind"
    assert list(last_word.read(odf(OTT)))[-1] == "sourceKind"


def test_refuses_a_macro_enabled_dotm() -> None:
    refused(lambda: last_word.read(retyped(MACRO_TEMPLATE)), "macro_enabled")


def test_refuses_a_dotx_that_carries_a_vba_project_anyway() -> None:
    data = retyped(TEMPLATE, {"word/vbaProject.bin": b"\xd0\xcf\x11\xe0 not really"})
    refused(lambda: last_word.read(data), "macro_enabled")


def test_still_reads_a_macro_enabled_document_as_before() -> None:
    assert "sourceKind" not in last_word.read(retyped(MACRO_DOC))


# ─── Hostile input ───────────────────────────────────────────────────────────


def test_refuses_a_template_whose_content_type_claims_another_family() -> None:
    refused(lambda: last_word.read(retyped(PPT_TEMPLATE)), "family_mismatch")

    parts: dict[str, bytes | str] = dict(entries(docx()))
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(  # type: ignore[union-attr]
        b"</Types>", b'<Override PartName="/ppt/presentation.xml" ContentType="' + PPT_TEMPLATE.encode() + b'"/></Types>')
    beside = rezip(parts)
    refused(lambda: last_word.read(beside), "family_mismatch")

    parts = dict(entries(docx()))
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(  # type: ignore[union-attr]
        b'ContentType="application/xml"', b'ContentType="' + TEMPLATE.encode() + b'"')
    default = rezip(parts)
    refused(lambda: last_word.read(default), "family_mismatch")


def test_refuses_an_odf_text_template_whose_body_is_a_spreadsheet() -> None:
    refused(lambda: OdtReader().read(odf(OTT, "spreadsheet")), "family_mismatch")


def test_refuses_an_odf_template_of_another_family() -> None:
    refused(lambda: OdtReader().read(odf("application/vnd.oasis.opendocument.presentation-template")), "family_mismatch")
    with pytest.raises(UnsupportedFormatException):
        last_word.read(odf("application/vnd.oasis.opendocument.spreadsheet-template", "spreadsheet"))


def test_refuses_an_odf_template_whose_manifest_names_another_family() -> None:
    refused(lambda: last_word.read(odf(OTT, "text", "application/vnd.oasis.opendocument.spreadsheet-template")), "family_mismatch")


def test_refuses_a_template_zip_bomb_reading_and_converting() -> None:
    bomb = retyped(TEMPLATE, {"word/media/bomb.bin": b"\x00" * (65 * 1024 * 1024)})
    archive_refused(lambda: last_word.read(bomb), "archive_limit")
    archive_refused(lambda: last_word.convert_template(bomb), "archive_limit")


def test_refuses_a_template_with_a_traversing_entry_name() -> None:
    evil = retyped(TEMPLATE, {"../evil.xml": "<x/>"})
    archive_refused(lambda: last_word.read(evil), "archive_path")
    archive_refused(lambda: last_word.convert_template(evil), "archive_path")


# ─── Converting ──────────────────────────────────────────────────────────────


def test_converts_a_dotx_to_a_docx_changing_only_the_content_type_value() -> None:
    source = dotx()
    converted = last_word.convert_template(source, "document")
    before, after = entries(source), entries(converted)
    assert list(after) == list(before)
    for name, data in before.items():
        if name == "[Content_Types].xml":
            assert after[name] == data.replace(TEMPLATE.encode(), DOC.encode())
        else:
            assert after[name] == data, f"{name} changed"
    assert "sourceKind" not in last_word.read(converted)


def test_round_trips_docx_to_dotx_and_back_byte_identical() -> None:
    original = docx()
    template = last_word.convert_template(original, "template")
    back = last_word.convert_template(template, "document")
    assert last_word.read(template)["sourceKind"] == "template"
    assert entries(back) == entries(original)

    # Untouched parts are copied as RECORDS, not re-encoded: same compression.
    want = {k: v for k, v in methods(original).items() if k != "[Content_Types].xml"}
    got = {k: v for k, v in methods(template).items() if k != "[Content_Types].xml"}
    assert got == want


def test_untouched_members_are_the_original_raw_records() -> None:
    source = dotx()
    converted = last_word.convert_template(source)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        for info in archive.infolist():
            if info.filename == "[Content_Types].xml":
                continue
            start = info.header_offset
            name_len, extra_len = int.from_bytes(source[start + 26:start + 28], "little"), int.from_bytes(source[start + 28:start + 30], "little")
            record = source[start:start + 30 + name_len + extra_len + info.compress_size]
            assert record in converted, f"{info.filename} was re-encoded"


def test_flips_direction_when_no_target_is_given() -> None:
    assert last_word.read(last_word.convert_template(docx()))["sourceKind"] == "template"
    assert "sourceKind" not in last_word.read(last_word.convert_template(dotx()))


def test_converts_odt_to_ott_keeping_mimetype_first_stored_and_extra_free() -> None:
    source = odf(ODT)
    converted = last_word.convert_template(source, "template")
    before, after = entries(source), entries(converted)
    assert list(after) == list(before)
    assert next(iter(after)) == "mimetype"
    assert after["mimetype"] == OTT.encode()
    assert methods(converted)["mimetype"] == zipfile.ZIP_STORED
    # The first local header: name `mimetype` at byte 30, extra length 0.
    assert converted[:4] == b"PK\x03\x04"
    assert int.from_bytes(converted[28:30], "little") == 0
    assert converted[30:38] == b"mimetype"
    assert converted[38:38 + len(OTT)] == OTT.encode()

    # ODF 1.3 Part 2 section 3.3: the manifest root media-type moves with it.
    assert after["META-INF/manifest.xml"] == before["META-INF/manifest.xml"].replace(
        b'manifest:full-path="/" manifest:version="1.3" manifest:media-type="' + ODT.encode() + b'"',
        b'manifest:full-path="/" manifest:version="1.3" manifest:media-type="' + OTT.encode() + b'"',
    )
    for name, data in before.items():
        if name not in ("mimetype", "META-INF/manifest.xml"):
            assert after[name] == data, f"{name} changed"

    assert last_word.read(converted)["sourceKind"] == "template"
    assert entries(last_word.convert_template(converted, "document")) == before


def test_refuses_conversions_it_must_not_make() -> None:
    refused(lambda: last_word.convert_template(dotx(), "template"), "already_template")
    refused(lambda: last_word.convert_template(docx(), "document"), "already_document")
    refused(lambda: last_word.convert_template(retyped(MACRO_TEMPLATE), "document"), "macro_enabled")
    refused(lambda: last_word.convert_template(retyped(MACRO_DOC), "template"), "macro_enabled")
    refused(lambda: last_word.convert_template(docx(), "spreadsheet"), "bad_target")
    refused(lambda: last_word.convert_template(retyped(PPT_TEMPLATE)), "family_mismatch")


def test_refuses_an_odf_package_whose_mimetype_is_not_stored_first() -> None:
    parts: dict[str, bytes | str] = dict(entries(odf(ODT)))
    mimetype = parts.pop("mimetype")
    late = rezip({**parts, "mimetype": mimetype})
    refused(lambda: last_word.convert_template(late), "odf_mimetype")


def test_refuses_a_declaration_it_cannot_rewrite_unambiguously() -> None:
    parts: dict[str, bytes | str] = dict(entries(dotx()))
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(  # type: ignore[union-attr]
        b'<Override PartName="/word/document.xml"',
        b'<!-- <Override PartName="/word/document.xml" ContentType="' + TEMPLATE.encode() + b'"/> --><Override PartName="/word/document.xml"',
    )
    decoy = rezip(parts)
    refused(lambda: last_word.convert_template(decoy), "not_convertible")


def test_names_a_spreadsheet_instead_of_converting_it() -> None:
    xlsx = rezip({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<workbook/>"})
    with pytest.raises(UnsupportedFormatException):
        last_word.convert_template(xlsx)


# ─── CLI ─────────────────────────────────────────────────────────────────────


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    return subprocess.run([sys.executable, "-m", "last_word", *args], capture_output=True, text=True, env=env, cwd=REPO, check=False)


def test_converts_through_the_template_convert_cli(tmp_path: Path) -> None:
    (tmp_path / "in.dotx").write_bytes(dotx())

    run = cli("template:convert", str(tmp_path / "in.dotx"), str(tmp_path / "out.docx"))
    assert run.returncode == 0, run.stderr
    assert "document" in run.stdout
    assert "sourceKind" not in last_word.read(tmp_path / "out.docx")

    run = cli("template:convert", str(tmp_path / "out.docx"), str(tmp_path / "out.docx"), "--to=template")
    assert run.returncode == 1 and "exists" in run.stderr

    run = cli("template:convert", str(tmp_path / "out.docx"), str(tmp_path / "back.dotx"), "--to=template")
    assert run.returncode == 0, run.stderr
    assert last_word.read(tmp_path / "back.dotx")["sourceKind"] == "template"

    (tmp_path / "macro.dotm").write_bytes(retyped(MACRO_TEMPLATE))
    run = cli("template:convert", str(tmp_path / "macro.dotm"), str(tmp_path / "macro.docx"))
    assert run.returncode == 1 and "macro_enabled" in run.stderr
    assert not (tmp_path / "macro.docx").exists()

    run = cli("template:convert")
    assert run.returncode == 2 and "Usage" in run.stderr


# ─── LibreOffice as an oracle (skipped where it is not installed) ────────────


def soffice() -> str | None:
    for candidate in (os.environ.get("LIBREOFFICE_BIN"), "C:/Program Files/LibreOffice/program/soffice.com",
                      "/usr/bin/soffice", "/usr/local/bin/soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def libreoffice(binary: str, source: Path, fmt: str, out_dir: Path) -> Path:
    """Headless convert; the profile lives in a SHORT temp path (long ones crash LO on Windows)."""
    profile = Path(tempfile.gettempdir()) / ("lwlo" + secrets.token_hex(3))
    try:
        run = subprocess.run([binary, "-env:UserInstallation=" + profile.as_uri(), "--headless", "--norestore",
                              "--convert-to", fmt, "--outdir", str(out_dir), str(source)],
                             capture_output=True, check=False, timeout=180)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    out = out_dir / (source.stem + "." + fmt.split(":")[0])
    assert run.returncode == 0 and out.is_file(), f"LibreOffice produced no {fmt}"
    return out


def test_reads_what_libreoffice_writes_and_libreoffice_reads_our_conversions() -> None:
    binary = soffice()
    if binary is None:
        pytest.skip("LibreOffice is not installed; the oracle check runs where it is.")
    work = Path(tempfile.gettempdir()) / ("lwo" + secrets.token_hex(3))
    work.mkdir()
    try:
        (work / "src.docx").write_bytes(docx())
        for template, document in (("dotx", "docx"), ("ott", "odt")):
            lo_template = libreoffice(binary, work / "src.docx", template, work)
            read = last_word.read(lo_template)
            assert read.get("sourceKind") == "template", f"LibreOffice .{template} not marked"
            assert "Dear reader" in repr(read)

            (work / template).mkdir()
            lo_back = libreoffice(binary, lo_template, document, work / template)
            assert "sourceKind" not in last_word.read(lo_back)

            ours = work / f"ours.{document}"
            ours.write_bytes(last_word.convert_template(lo_template, "document"))
            (work / f"ours-{template}").mkdir()
            text = libreoffice(binary, ours, "txt:Text", work / f"ours-{template}")
            assert "Dear reader" in text.read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(work, ignore_errors=True)

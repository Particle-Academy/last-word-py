"""`last-word` (and `python -m last_word`). Mirrors PHP `LastWord\\Cli` and
`bin/last-word`. One command today:

    last-word template:convert <input> <output> [--to=document|template] [--force]

Exit codes: 0 converted, 1 refused (the reason is printed), 2 usage.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import TextIO

from .agent import convert_template
from .archive import office_template
from .archive.source_archive import SourceArchive
from .exceptions import ArchiveRefused, OfficeTemplateRefused, UnsupportedFormatException

USAGE = (
    "Usage: last-word template:convert <input> <output> [--to=document|template] [--force]\n"
    "  Converts a .dotx/.ott template to its .docx/.odt twin, or back.\n"
    "  Without --to it flips whichever the input is.\n"
)


def run(args: list[str], stdout: TextIO, stderr: TextIO) -> int:
    args = list(args)
    command = args.pop(0) if args else None
    positional: list[str] = []
    to: str | None = None
    force = False
    for arg in args:
        if arg.startswith("--to="):
            to = arg[5:]
        elif arg == "--force":
            force = True
        elif arg.startswith("--"):
            stderr.write(f"Unknown option {arg}\n" + USAGE)
            return 2
        else:
            positional.append(arg)
    if command != "template:convert" or len(positional) != 2:
        stderr.write(USAGE)
        return 2
    source, output = Path(positional[0]), Path(positional[1])

    if output.exists() and not force:
        stderr.write(f"Refused: {output} already exists (pass --force to replace it).\n")
        return 1
    if source.exists() and output.exists() and source.resolve() == output.resolve():
        stderr.write("Refused: the output is the input.\n")
        return 1
    if not source.is_file():
        stderr.write(f"Refused (archive_path): {source} is not a file.\n")
        return 1

    try:
        converted = convert_template(source, to)
    except (OfficeTemplateRefused, ArchiveRefused) as error:
        stderr.write(f"Refused ({error.reason}): {error}\n")
        return 1
    except UnsupportedFormatException as error:
        stderr.write(f"Refused ({error.format}): {error}\n")
        return 1

    # Written beside the target and renamed, so a failure leaves no half file.
    temp = output.with_name(output.name + ".tmp-" + secrets.token_hex(4))
    try:
        temp.write_bytes(converted)
        os.replace(temp, output)
    except OSError:
        temp.unlink(missing_ok=True)
        stderr.write(f"Could not write {output}.\n")
        return 1
    kind = office_template.kind(SourceArchive.open(converted))
    stdout.write(f"Wrote {output} ({kind}).\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv, sys.stdout, sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

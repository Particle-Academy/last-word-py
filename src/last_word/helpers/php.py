"""PHP semantics Python does not share, written down once.

Byte-parity with the PHP writer is this port's acceptance test, so every place
the reference leans on a PHP primitive whose Python equivalent differs *has* to
go through a helper here. The differences are small, silent, and each one moves
output bytes.

The important one is `php_round`. **Never call the builtin `round()` anywhere in
this package** -- Python's is banker's rounding (`round(0.5) == 0`,
`round(2.5) == 2`) and PHP's is half away from zero (`1` and `3`). Image extents
and the read-back `widthPx`/`heightPx` both round, so the builtin would land a
one-pixel disagreement on exactly the values a test picks.
"""

from __future__ import annotations

import math
import re
from typing import Any

# PHP's trim() default character mask -- notably NOT Python's str.strip(), which
# strips the whole Unicode whitespace class.
PHP_TRIM_CHARS = " \t\n\r\0\x0b"

# is_numeric()'s grammar as of PHP 8: leading AND trailing whitespace allowed,
# decimal or hex-free exponent forms, no bare "." and no lone sign.
_NUMERIC_RE = re.compile(
    r"^[ \t\n\r\v\f]*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[ \t\n\r\v\f]*$"
)


def php_round(value: float) -> float:
    """PHP's `round()`: half away from zero, not half to even."""
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def php_int_round(value: float) -> int:
    """`(int) round($value)` -- the exact shape the PHP writer/reader use."""
    return int(php_round(value))


def php_truthy(value: Any) -> bool:
    """PHP's `!empty($value)`.

    Differs from `bool()` on exactly one input that matters here: the string
    `"0"`, which PHP considers empty and Python considers true.
    """
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value != "" and value != "0"
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def is_numeric(value: Any) -> bool:
    """PHP's `is_numeric()`. Booleans are NOT numeric in PHP."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not (isinstance(value, float) and (math.isnan(value) or math.isinf(value)))
    if isinstance(value, str):
        return _NUMERIC_RE.match(value) is not None
    return False


def php_float(value: Any) -> float:
    """PHP's `(float)` cast for the values that reach it here."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.match(r"^[ \t\n\r\v\f]*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value)
        return float(match.group(0)) if match else 0.0
    return 0.0


def php_str(value: Any) -> str:
    """PHP's `(string)` cast: null -> "", true -> "1", false -> ""."""
    if value is None or value is False:
        return ""
    if value is True:
        return "1"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        # PHP's default `precision` ini is 14 significant digits.
        return f"{value:.14G}"
    return str(value)


def php_trim(value: str) -> str:
    return value.strip(PHP_TRIM_CHARS)


def php_rtrim(value: str) -> str:
    return value.rstrip(PHP_TRIM_CHARS)


def is_scalar(value: Any) -> bool:
    """PHP's `is_scalar()`: int, float, string, bool -- and nothing else."""
    return isinstance(value, (int, float, str, bool))


def is_list(value: Any) -> bool:
    """PHP's `array_is_list()` for the JSON shapes this model uses.

    A JSON array decodes to a Python `list`; a JSON object decodes to a `dict`.
    So "is a list-shaped array" is simply "is a list".
    """
    return isinstance(value, list)


def debug_type(value: Any) -> str:
    """PHP's `get_debug_type()` -- it appears verbatim in validator messages."""
    if value is None:
        return "null"
    if value is True or value is False:
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, dict)):
        return "array"
    return type(value).__name__

"""PHP array semantics for the op engine, written down once.

`Ops\\DocReducer` and `Ops\\DocDiff` in the PHP reference run on PHP arrays, and
a PHP array is not a Python dict. The differences that decide what the ops do
are reproduced here rather than approximated:

* **One array is both a list and a map.** `[a, b]` and `{"0": a, "1": b}` are the
  SAME value in PHP (`json_decode` turns a numeric string key into an int key),
  and so are `[]` and `{}`. :func:`pairs` normalises keys the way PHP stores
  them, and :func:`canon` writes a map keyed 0..n-1 in order as a list, exactly
  as `json_encode` does.
* **`===` is typed.** `1 === 1.0` and `true === 1` are false. Python's `==` says
  both are true, because `bool` subclasses `int`. :func:`identical` and
  :func:`position` check `bool` before `int`, every time.
* **`json_encode` fails on NaN, INF, invalid UTF-8 and nesting past its depth**,
  and since last-word 0.6.1 `DocDiff::canon()` throws `JsonException` there.
  :func:`canon` raises `ValueError` for the same inputs, a lone surrogate
  standing in for invalid UTF-8, at the same depth: 4096 arrays, an empty one
  counting as a level. It walks an explicit stack, because Python's default
  recursion limit (1000) would otherwise decide instead of PHP's depth.

Where a PHP integer would have been a float (a JSON integer past 2**63), this
module treats the Python int as that float, which is what PHP was handed.

The Node port's counterpart is `src/ops/php.ts`; PHP needs none. It lives beside
the ops rather than in `helpers/php.py` because only the ops run on PHP arrays:
the writer and readers take a document shape the validator has already fixed.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

PHP_INT_MAX = 2**63 - 1
PHP_INT_MIN = -(2**63)

#: The depth `DocDiff::canon()` passes `json_encode`.
JSON_MAX_DEPTH = 4096

# `ctype_digit`: one or more ASCII digits. Not `str.isdigit()`, which accepts
# every Unicode digit ("٣", "²").
_DIGITS = re.compile(r"[0-9]+", re.ASCII)

# A string PHP turns into an integer array key: canonical decimal, no "+", no
# leading zero, no "-0". Range-checked separately.
_INT_KEY = re.compile(r"(?:0|-?[1-9][0-9]*)", re.ASCII)


def is_array(value: Any) -> bool:
    """PHP `is_array`: a list and a map are both arrays."""
    return isinstance(value, (list, tuple, dict))


def ctype_digit(value: Any) -> bool:
    """PHP `ctype_digit` on a string: non-empty and ASCII digits only."""
    return isinstance(value, str) and _DIGITS.fullmatch(value) is not None


def digits_to_int(value: str) -> int:
    """`(int) $digits` for a `ctype_digit` string: saturates at PHP_INT_MAX."""
    return min(int(value), PHP_INT_MAX)


def php_key(key: Any) -> Any:
    """The key PHP stores for `$array[$key]`."""
    if isinstance(key, bool):  # before int, always
        return int(key)
    if isinstance(key, int):
        return key
    if isinstance(key, str):
        if _INT_KEY.fullmatch(key):
            number = int(key)
            if PHP_INT_MIN <= number <= PHP_INT_MAX:
                return number
        return key
    if key is None:
        return ""
    raise TypeError(f"[last-word] a {type(key).__name__} cannot be an array key")


def _same_key(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def pairs(value: Any) -> list[tuple[Any, Any]]:
    """`foreach ($array as $key => $item)`, keys as PHP stores them.

    Two Python keys PHP would store as one (`"1"` and `1`) collapse the way a
    second assignment does in PHP: the value is replaced, the position kept.
    """
    if isinstance(value, (list, tuple)):
        return list(enumerate(value))
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            out[php_key(key)] = item
        return list(out.items())
    return []


def is_list_pairs(items: list[tuple[Any, Any]]) -> bool:
    """`array_is_list`: keys are exactly 0..n-1, in order."""
    return all(
        isinstance(key, int) and not isinstance(key, bool) and key == position
        for position, (key, _) in enumerate(items)
    )


def is_list(value: Any) -> bool:
    """`is_array($value) && array_is_list($value)`."""
    if isinstance(value, (list, tuple)):
        return True
    return isinstance(value, dict) and is_list_pairs(pairs(value))


def values(value: Any) -> list[Any]:
    """`array_values`."""
    if isinstance(value, list):
        return list(value)
    return [item for _, item in pairs(value)]


def count(value: Any) -> int:
    """`count($array)`."""
    return len(value) if isinstance(value, (list, tuple)) else len(pairs(value))


def get(array: Any, key: Any) -> Any:
    """`$array[$key] ?? null`: null when `array` is not an array, when the key is
    absent, and when the value IS null."""
    stored = php_key(key)
    if isinstance(array, (list, tuple)):
        if isinstance(stored, int) and 0 <= stored < len(array):
            return array[stored]
        return None
    if isinstance(array, dict):
        if isinstance(stored, str):
            # Only the string itself normalises to a non-numeric string key.
            return array.get(stored)
        found = None
        for existing, item in array.items():
            if _same_key(php_key(existing), stored):
                found = item  # a later duplicate wins, as a second assignment does
        return found
    return None


def has(array: Any, key: Any) -> bool:
    """`array_key_exists($key, $array)`: a null value counts."""
    stored = php_key(key)
    if isinstance(array, (list, tuple)):
        return isinstance(stored, int) and 0 <= stored < len(array)
    if isinstance(array, dict):
        if isinstance(stored, str):
            return stored in array
        return any(_same_key(php_key(existing), stored) for existing in array)
    return False


def with_key(node: Any, key: Any, value: Any) -> Any:
    """`$node[$key] = $value` on a copy; `node` itself is not modified.

    A list given a key that is not the next index becomes a map, as a PHP list
    does. New keys are written as strings, the way JSON carries them.
    """
    stored = php_key(key)
    if isinstance(node, (list, tuple)):
        if isinstance(stored, int) and 0 <= stored < len(node):
            out = list(node)
            out[stored] = value
            return out
        if isinstance(stored, int) and stored == len(node):
            return [*node, value]
        mapped: dict[Any, Any] = {str(index): item for index, item in enumerate(node)}
        mapped[str(stored)] = value
        return mapped
    if isinstance(node, dict):
        result: dict[Any, Any] = {}
        replaced = False
        for existing, item in node.items():
            if _same_key(php_key(existing), stored):
                if not replaced:
                    result[existing] = value
                    replaced = True
                continue
            result[existing] = item
        if not replaced:
            result[str(stored)] = value
        return result
    raise TypeError(f"[last-word] cannot set a key on a {type(node).__name__}")


def without_key(node: Any, key: Any) -> Any:
    """`unset($node[$key])` on a copy. Removing anything but a list's last entry
    leaves a map, as in PHP."""
    if not has(node, key):
        return node
    stored = php_key(key)
    if isinstance(node, (list, tuple)):
        if stored == len(node) - 1:
            return list(node[:-1])
        return {str(index): item for index, item in enumerate(node) if index != stored}
    return {existing: item for existing, item in node.items() if not _same_key(php_key(existing), stored)}


def position(value: Any) -> int | None:
    """PHP `DocReducer::position()`: an int, or a string of digits; else None.

    `True` is not an int (`is_int(true)` is false), nor is `2.0`, nor an integer
    outside PHP's range, which `json_decode` would have made a float. A digit
    string past the range saturates, as `(int)` does.
    """
    if isinstance(value, bool):  # before int, always
        return None
    if isinstance(value, int):
        return value if PHP_INT_MIN <= value <= PHP_INT_MAX else None
    if ctype_digit(value):
        return digits_to_int(value)
    return None


def identical(a: Any, b: Any) -> bool:
    """PHP `===`."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):  # before int, always
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if is_array(a) or is_array(b):
        if not (is_array(a) and is_array(b)):
            return False
        left, right = pairs(a), pairs(b)
        return len(left) == len(right) and all(
            _same_key(ka, kb) and identical(va, vb) for (ka, va), (kb, vb) in zip(left, right)
        )
    a, b = _php_scalar(a), _php_scalar(b)
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, float) and isinstance(b, float) and a == b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    return a is b


def _php_scalar(value: Any) -> Any:
    """What PHP holds for a decoded scalar: an int past its range was a float."""
    if isinstance(value, int) and not isinstance(value, bool) and not PHP_INT_MIN <= value <= PHP_INT_MAX:
        try:
            return float(value)
        except OverflowError:
            return math.inf
    return value


def canon(value: Any) -> str:
    """`DocDiff::canon()`: JSON with map keys sorted and list order kept.

    Only equality between two canon strings is ever used, so what matters is
    that the relation is PHP's: `1` and `1.0` differ, `true` and `1` differ, `[]`
    and `{}` are the same, and `{"0": x}` is `[x]`. The text itself differs from
    PHP's bytes (`1e+25` where PHP writes `1.0e+25`).

    Raises `ValueError` where PHP throws `JsonException`: NaN or an infinity, an
    int too large for a float (`json_decode`'s INF), a lone surrogate in a string
    or a key (invalid UTF-8 in PHP), and more than 4096 nested arrays. The walk
    is a loop over an explicit stack, not recursion, so that limit is PHP's and
    not Python's recursion limit.
    """
    if not is_array(value):
        return _canon_scalar(value)

    stack: list[_Frame] = [_Frame(value, None, 1)]

    while True:
        frame = stack[-1]

        if frame.position < len(frame.items):
            key, item = frame.items[frame.position]
            frame.position += 1
            if is_array(item):
                stack.append(_Frame(item, key, len(stack) + 1))
            else:
                frame.parts.append(frame.member(key, _canon_scalar(item)))
            continue

        stack.pop()
        text = "[" + ",".join(frame.parts) + "]" if frame.is_list else "{" + ",".join(frame.parts) + "}"
        if not stack:
            return text
        stack[-1].parts.append(stack[-1].member(frame.key, text))


class _Frame:
    """One array of :func:`canon`'s walk: its pairs in output order, and the text so far."""

    __slots__ = ("items", "key", "position", "parts", "is_list")

    def __init__(self, value: Any, key: Any, depth: int) -> None:
        if depth > JSON_MAX_DEPTH:
            raise ValueError("[last-word] cannot compare a value JSON cannot hold: Maximum stack depth exceeded")
        items = pairs(value)
        if not is_list_pairs(items):
            # ksort($value, SORT_STRING). Code-point order is UTF-8 byte order.
            items.sort(key=lambda pair: str(pair[0]))
        self.items = items
        self.key = key
        self.position = 0
        self.parts: list[str] = []
        # json_encode decides list-or-object AFTER the sort: {"1": b, "0": a} is [a, b].
        self.is_list = is_list_pairs(items)

    def member(self, key: Any, text: str) -> str:
        return text if self.is_list else _canon_string(str(key)) + ":" + text


def _canon_scalar(value: Any) -> str:
    if value is None or isinstance(value, bool):  # bool before int, always
        return json.dumps(value)
    if isinstance(value, int):
        if PHP_INT_MIN <= value <= PHP_INT_MAX:
            return str(value)
        try:
            value = float(value)
        except OverflowError as error:
            raise ValueError("[last-word] cannot compare a value JSON cannot hold: Inf and NaN cannot be JSON encoded") from error
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("[last-word] cannot compare a value JSON cannot hold: Inf and NaN cannot be JSON encoded")
        return json.dumps(value)
    if isinstance(value, str):
        return _canon_string(value)
    raise TypeError(f"[last-word] a {type(value).__name__} is not a JSON value")


def _canon_string(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(
            "[last-word] cannot compare a value JSON cannot hold: Malformed UTF-8 characters, possibly incorrectly encoded"
        ) from error
    return json.dumps(value, ensure_ascii=False)


def php_json_view(value: Any) -> Any:
    """What `json_encode` would print for `value`, key order kept, as Python data.

    For comparing this port's output with PHP's: PHP prints an array keyed 0..n-1
    as a list, an empty map as `[]`, and an int past its range as the float it was.
    """
    if is_array(value):
        items = pairs(value)
        if is_list_pairs(items):
            return [php_json_view(item) for _, item in items]
        return {str(key): php_json_view(item) for key, item in items}
    return _php_scalar(value)

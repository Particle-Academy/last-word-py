"""The PHP-semantics shims -- the smallest tests here and the ones with the
widest blast radius.

`php_round` in particular: Python's builtin `round()` is banker's rounding and
PHP's is half away from zero. Every image extent and every read-back
`widthPx`/`heightPx` passes through it, so getting this wrong is a one-pixel
disagreement with the reference on exactly the values a fixture picks -- and it
would look like a writer bug, not a rounding-mode bug.
"""

from __future__ import annotations

import pytest

from last_word.helpers import xml
from last_word.helpers.php import (
    is_numeric,
    php_round,
    php_str,
    php_truthy,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.5, 1),
        (1.5, 2),
        (2.5, 3),
        (3.5, 4),
        (-0.5, -1),
        (-1.5, -2),
        (-2.5, -3),
        (249.6, 250),
        (0.4, 0),
        (0.0, 0),
    ],
)
def test_php_round_is_half_away_from_zero(value: float, expected: int) -> None:
    assert php_round(value) == expected


def test_php_round_differs_from_the_builtin_exactly_where_it_must() -> None:
    """The builtin is wrong on every .5 at an even integer.

    Written as a contrast rather than a bare table so the reason the helper
    exists is visible at the failure site.
    """
    assert round(0.5) == 0 and php_round(0.5) == 1
    assert round(2.5) == 2 and php_round(2.5) == 3
    assert round(-0.5) == 0 and php_round(-0.5) == -1


def test_php_truthy_treats_the_string_zero_as_empty() -> None:
    assert php_truthy("0") is False
    assert bool("0") is True

    assert php_truthy("") is False
    assert php_truthy(0) is False
    assert php_truthy(None) is False
    assert php_truthy([]) is False
    assert php_truthy("false") is True
    assert php_truthy(True) is True


def test_is_numeric_matches_phps_grammar() -> None:
    assert is_numeric("12")
    assert is_numeric(" 12 ")  # PHP 8 allows trailing whitespace
    assert is_numeric("1e5")
    assert is_numeric("1.")
    assert is_numeric(3)
    assert is_numeric(3.5)

    assert not is_numeric(True)  # booleans are NOT numeric in PHP
    assert not is_numeric("abc")
    assert not is_numeric("")
    assert not is_numeric(".")
    assert not is_numeric(None)


def test_php_str_casts_like_php() -> None:
    assert php_str(None) == ""
    assert php_str(True) == "1"
    assert php_str(False) == ""
    assert php_str(5) == "5"
    assert php_str(1.0) == "1"
    assert php_str(1.5) == "1.5"
    assert php_str("x") == "x"


def test_xml_text_escapes_the_ent_compat_set_and_leaves_apostrophes() -> None:
    # htmlspecialchars($s, ENT_XML1 | ENT_COMPAT) -- no apostrophe.
    assert xml.text("a & b < c > d \" e ' f") == "a &amp; b &lt; c &gt; d &quot; e ' f"


def test_xml_attr_escapes_apostrophes_the_xml_way() -> None:
    # ENT_XML1 emits &apos;, not the HTML &#039;.
    assert xml.attr("it's") == "it&apos;s"
    assert xml.attr('a & "b"') == "a &amp; &quot;b&quot;"


def test_xml_escaping_does_not_double_escape_ampersands() -> None:
    assert xml.text("&amp;") == "&amp;amp;"


def test_the_xml_declaration_is_the_docx_one() -> None:
    assert xml.declaration() == '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    assert xml.declaration(False) == '<?xml version="1.0" encoding="UTF-8"?>\n'

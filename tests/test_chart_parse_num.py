"""The chart reader's number pattern: linear on a long run of digits, same answers."""

import time

import pytest

from last_word.helpers.chart import parse_num


def test_reads_a_very_long_run_of_digits_in_linear_time() -> None:
    started = time.perf_counter()
    assert parse_num("9" * 100_000 + "x") == 0
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("5", 5),
        ("5.", 5),
        ("5.25", 5.25),
        (".5", 0.5),
        ("-5.5", -5.5),
        ("+5", 5),
        ("1e2", 100),
        ("5.e2", 500),
        (".", 0),
        ("0x10", 0),
        ("1.2.3", 0),
    ],
)
def test_reads_every_shape_of_decimal_and_anything_else_as_0(text: str, want: float) -> None:
    assert parse_num(text) == want

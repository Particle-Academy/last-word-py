"""Suite-wide fixtures.

`pythonpath = ["src"]` in pyproject.toml puts the package on the path, so a
bare checkout with nothing but pytest can run the suite -- the same property
`npm test` has for the Node mirror.
"""

from __future__ import annotations

import pytest

from tests import _oracle


def pytest_configure(config: pytest.Config) -> None:
    """Announce the oracle's status once, loudly, at the top of every run.

    A suite that quietly stops comparing anything reads exactly like a suite
    that compares everything. This line is the difference.
    """
    _oracle.require_oracle()
    ok, why = _oracle.oracle_available()
    config.stash  # noqa: B018 - touch, keeps type checkers quiet
    banner = (
        "cross-runtime oracle: PHP available"
        if ok
        else f"cross-runtime oracle: UNAVAILABLE - {why} (parity tests will SKIP; they FAIL under CI)"
    )
    config.addinivalue_line("markers", "parity: compares this port against the PHP reference")
    print(f"\n[last_word] {banner}")


@pytest.fixture(scope="session")
def php_oracle():
    """The PHP writer, or a skip with the reason spelled out."""
    ok, why = _oracle.oracle_available()
    if not ok:
        pytest.skip(f"PHP oracle unavailable: {why}")
    return _oracle

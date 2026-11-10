"""The agent guide names every public Agent function, or the function is on the
allowlist below with the reason it is left out.

A function the guide never names is a function no model reading the guide will
use, so this fails the build when a release adds one and the guide is not
brought along.

The surface is every function defined in ``last_word.agent`` that the package
exports in ``__all__``. "Names" means written as code: ``Agent::x``,
``Agent.x`` or a backticked ``x``, in either the snake_case name or the
camelCase one the shared guide uses (``validate_and_repair`` is named by
``validateAndRepair``). The PHP and Node packages carry the same test.
"""

from __future__ import annotations

import inspect
import re

import last_word
from last_word import agent, guide

ALLOWLIST: dict[str, str] = {
    "guide": "the guide itself: a model reading it already has it",
    "version": "package metadata; it has no bearing on building or reading a document",
}


def _surface() -> list[str]:
    return [
        name
        for name, fn in inspect.getmembers(agent, inspect.isfunction)
        if fn.__module__ == agent.__name__ and name in last_word.__all__
    ]


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _names(text: str, name: str) -> bool:
    return any(
        re.search(r"(Agent::|Agent\.|`)" + re.escape(form) + r"\b", text) is not None
        for form in {name, _camel(name)}
    )


def test_names_every_public_agent_function_or_allowlists_it() -> None:
    text = guide()
    missing = [n for n in _surface() if n not in ALLOWLIST and not _names(text, n)]
    assert missing == [], f"agent_guide.md never names: {', '.join(missing)}"


def test_allowlist_entries_exist_have_a_reason_and_are_not_named() -> None:
    text = guide()
    surface = _surface()
    for name, reason in ALLOWLIST.items():
        assert name in surface
        assert reason.strip() != ""
        assert not _names(text, name), f"{name} is named in the guide; drop it from the allowlist"

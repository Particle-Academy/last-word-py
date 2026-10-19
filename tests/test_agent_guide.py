"""The agent guide ships, and is the same bytes in all three engines.

Each engine pins this hash over LF-normalised content; edit all three copies,
then all three constants.
"""

from __future__ import annotations

import hashlib

from last_word import guide

AGENT_GUIDE_SHA256 = "dca27c9230f7c74fb6e8ef5ed2f4546cff2fb41ebbfca55eb2b4723f6eb82faf"


def test_returns_the_guide_and_it_is_the_shared_one() -> None:
    text = guide()
    assert hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest() == AGENT_GUIDE_SHA256
    assert text.startswith("# Agent guide:")

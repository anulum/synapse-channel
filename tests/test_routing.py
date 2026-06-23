# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for task-class classification and tiered routing

from __future__ import annotations

import pytest

from synapse_channel.client.routing import TaskClass, TieredChatClient, classify


class _Echo:
    """A stub backend whose reply names its tag and echoes the prompt."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        return f"{self.tag}:{user_prompt}"


# --- classify ----------------------------------------------------------------


def test_short_prompt_is_rule() -> None:
    assert classify("hi") == TaskClass.RULE
    assert classify("") == TaskClass.RULE
    assert classify("x" * 24) == TaskClass.RULE  # exactly at the boundary


def test_medium_prompt_is_slm() -> None:
    assert classify("what is the current status of the parser task?") == TaskClass.SLM


def test_heavy_keyword_makes_a_long_prompt_heavy() -> None:
    assert classify("design a resilient coordination layer for agents") == TaskClass.HEAVY


def test_long_prompt_without_keyword_is_heavy() -> None:
    assert classify("a " * 130) == TaskClass.HEAVY  # >= 240 chars, no keyword


def test_thresholds_are_tunable() -> None:
    # A tiny heavy threshold and no keyword pushes a medium prompt to heavy.
    assert classify("hello there teammate", rule_max_chars=2, heavy_min_chars=5) == TaskClass.HEAVY


# --- TieredChatClient --------------------------------------------------------


def _tiered() -> TieredChatClient:
    return TieredChatClient(
        {TaskClass.RULE: _Echo("R"), TaskClass.SLM: _Echo("S"), TaskClass.HEAVY: _Echo("H")}
    )


def test_tiered_dispatches_by_class() -> None:
    tiered = _tiered()
    assert tiered.generate(system_prompt="", user_prompt="hi").startswith("R:")
    assert tiered.last_class == TaskClass.RULE
    assert tiered.generate(
        system_prompt="", user_prompt="design a coordination system for agents"
    ).startswith("H:")
    assert tiered.last_class == TaskClass.HEAVY


def test_tiered_falls_back_to_default_when_class_absent() -> None:
    # Only SLM registered; a rule-classified prompt falls back to the default.
    tiered = TieredChatClient({TaskClass.SLM: _Echo("S")}, default_class=TaskClass.SLM)
    assert tiered.generate(system_prompt="", user_prompt="hi").startswith("S:")


def test_tiered_requires_a_default_backend() -> None:
    with pytest.raises(ValueError, match="default class"):
        TieredChatClient({TaskClass.RULE: _Echo("R")}, default_class=TaskClass.SLM)


def test_route_exposes_the_class_decision() -> None:
    assert _tiered().route("hi") == TaskClass.RULE


def test_tiered_uses_an_injected_classifier() -> None:
    tiered = TieredChatClient({TaskClass.SLM: _Echo("S")}, classifier=lambda prompt: TaskClass.SLM)
    assert tiered.generate(system_prompt="", user_prompt="design heavy thing").startswith("S:")

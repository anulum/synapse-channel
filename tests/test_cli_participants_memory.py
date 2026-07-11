# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for shared Participant memory CLI configuration
"""Pin opt-in defaults, invalid combinations, parser safety, and seat wrapping."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from synapse_channel.cli import build_parser
from synapse_channel.cli_participants_memory import (
    DEFAULT_MEMORY_MAX_CHARS,
    DEFAULT_MEMORY_TIMEOUT,
    DEFAULT_MEMORY_TOP_K,
    wrap_participants,
)
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.memory_participant import MemoryAugmentedParticipant
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth
from synapse_channel.participants.remanentia_http import RemanentiaHttpRecall


@dataclass(frozen=True)
class _Seat:
    identity: str

    @property
    def channel(self) -> ParticipantChannel:
        return ParticipantChannel.HEADLESS

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        raise AssertionError("configuration tests never take a turn")

    def health(self) -> ParticipantHealth:
        return ParticipantHealth(self.identity, self.channel, True, "ready")


def _args(**changes: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "memory_url": None,
        "memory_token_file": None,
        "memory_timeout": None,
        "memory_top_k": None,
        "memory_max_chars": None,
    }
    defaults.update(changes)
    return argparse.Namespace(**defaults)


def test_disabled_memory_returns_the_exact_seat_objects() -> None:
    seats = [_Seat("a"), _Seat("b")]
    wrapped = wrap_participants(seats, _args())
    assert wrapped == seats
    assert all(actual is expected for actual, expected in zip(wrapped, seats, strict=True))


@pytest.mark.parametrize(
    "changes",
    [
        {"memory_token_file": "/tmp/token"},
        {"memory_timeout": 1.0},
        {"memory_top_k": 2},
        {"memory_max_chars": 1000},
    ],
)
def test_tuning_without_a_url_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="require --memory-url"):
        wrap_participants([_Seat("a")], _args(**changes))


def test_enabled_memory_wraps_every_seat_with_one_shared_client() -> None:
    seats = [_Seat("a"), _Seat("b")]
    wrapped = wrap_participants(seats, _args(memory_url="http://127.0.0.1:8001"))
    first = wrapped[0]
    second = wrapped[1]
    assert isinstance(first, MemoryAugmentedParticipant)
    assert isinstance(second, MemoryAugmentedParticipant)
    assert first.participant is seats[0]
    assert second.participant is seats[1]
    assert first.recall is second.recall
    assert isinstance(first.recall, RemanentiaHttpRecall)
    assert first.policy.timeout_seconds == DEFAULT_MEMORY_TIMEOUT
    assert first.policy.top_k == DEFAULT_MEMORY_TOP_K
    assert first.policy.max_chars == DEFAULT_MEMORY_MAX_CHARS


def test_explicit_memory_bounds_and_token_path_reach_the_wrapper() -> None:
    wrapped = wrap_participants(
        [_Seat("a")],
        _args(
            memory_url="https://memory.example.test",
            memory_token_file="~/memory.token",
            memory_timeout=3.5,
            memory_top_k=7,
            memory_max_chars=2048,
        ),
    )[0]
    assert isinstance(wrapped, MemoryAugmentedParticipant)
    assert wrapped.policy.timeout_seconds == 3.5
    assert wrapped.policy.top_k == 7
    assert wrapped.policy.max_chars == 2048
    recall = wrapped.recall
    assert isinstance(recall, RemanentiaHttpRecall)
    assert recall.token_file is not None
    assert str(recall.token_file).endswith("memory.token")


@pytest.mark.parametrize(
    "changes",
    [
        {"memory_url": "ftp://memory.example.test"},
        {"memory_url": "http://127.0.0.1:8001", "memory_timeout": 0},
        {"memory_url": "http://127.0.0.1:8001", "memory_top_k": 0},
        {"memory_url": "http://127.0.0.1:8001", "memory_max_chars": 10},
    ],
)
def test_invalid_enabled_configuration_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        wrap_participants([_Seat("a")], _args(**changes))


@pytest.mark.parametrize(
    "argv",
    [
        ["participant", "ask", "claude", "q"],
        ["participant", "exchange", "q", "claude", "codex"],
        ["participant", "convene", "q", "claude", "codex"],
    ],
)
def test_all_three_turn_commands_share_the_memory_defaults(argv: list[str]) -> None:
    args = build_parser(command="participant").parse_args(argv)
    assert args.memory_url is None
    assert args.memory_token_file is None
    assert args.memory_timeout is None
    assert args.memory_top_k is None
    assert args.memory_max_chars is None


@pytest.mark.parametrize(
    "argv",
    [
        ["participant", "ask", "claude", "q"],
        ["participant", "exchange", "q", "claude", "codex"],
        ["participant", "convene", "q", "claude", "codex"],
    ],
)
def test_memory_token_literal_and_option_abbreviation_are_refused(argv: list[str]) -> None:
    parser = build_parser(command="participant")
    with pytest.raises(SystemExit):
        parser.parse_args([*argv, "--memory-token", "secret"])
    with pytest.raises(SystemExit):
        parser.parse_args([*argv, "--memory-u", "http://127.0.0.1:8001"])


def test_parser_carries_explicit_values_for_each_command() -> None:
    flags = [
        "--memory-url",
        "http://127.0.0.1:8001",
        "--memory-token-file",
        "/run/secrets/memory",
        "--memory-timeout",
        "1.5",
        "--memory-top-k",
        "4",
        "--memory-max-chars",
        "2000",
    ]
    for base in (
        ["participant", "ask", "claude", "q"],
        ["participant", "exchange", "q", "claude", "codex"],
        ["participant", "convene", "q", "claude", "codex"],
    ):
        args = build_parser(command="participant").parse_args([*base, *flags])
        assert SimpleNamespace(
            url=args.memory_url,
            token=args.memory_token_file,
            timeout=args.memory_timeout,
            top_k=args.memory_top_k,
            max_chars=args.memory_max_chars,
        ) == SimpleNamespace(
            url="http://127.0.0.1:8001",
            token="/run/secrets/memory",
            timeout=1.5,
            top_k=4,
            max_chars=2000,
        )

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the deliberation subcommands of the participant CLI

"""Tests for ``synapse participant exchange`` and ``synapse participant convene``.

Two scripted providers are injected into the registry so panels mix seats with
independently shaped behaviour (availability, errors, abstentions, metered cost)
while no test ever drives a real provider CLI or model server. The library
layers under the commands (exchange, convene, modes) have their own suites;
these tests pin the CLI contract — spec parsing, seat numbering, exit codes,
live output markers, and the JSON transcript shapes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from synapse_channel.cli import build_parser
from synapse_channel.cli_participants import PROVIDERS, refusal_for
from synapse_channel.cli_participants_deliberate import (
    _cmd_convene,
    _cmd_exchange,
    build_deliberants,
    parse_spec,
)
from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.exchange import REACTION_DIRECTIVE
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)


class _ScriptedSeat:
    """A fabric participant answering from a script instead of a provider."""

    def __init__(
        self,
        identity: str,
        *,
        model: str = "",
        timeout: float = 0.0,
        available: bool = True,
        answer: str = "scripted answer",
        is_error: bool = False,
        abstained: bool = False,
        reason: str = "",
        cost_usd: float = 0.0,
    ) -> None:
        self._identity = identity
        self._model = model
        self._timeout = timeout
        self._available = available
        self._answer = answer
        self._is_error = is_error
        self._abstained = abstained
        self._reason = reason
        self._cost_usd = cost_usd
        self.requests: list[TurnRequest] = []

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        return ParticipantHealth(
            identity=self._identity,
            channel=ParticipantChannel.HEADLESS,
            available=self._available,
            detail="scripted",
        )

    async def take_turn(self, request: TurnRequest) -> Any:
        self.requests.append(request)
        return {
            "kind": "participant.turn_result",
            "participant": self._identity,
            "channel": "headless",
            "topic_id": request.topic_id,
            "answer": self._answer,
            "rationale": "",
            "abstained": self._abstained,
            "is_error": self._is_error,
            "reason": self._reason,
            "session": "",
            "cost_usd": self._cost_usd,
            "stop_reason": "",
            "model": self._model,
            "input_tokens": 0,
            "output_tokens": 0,
            "rate_limit_utilisation": None,
        }


@pytest.fixture
def fabric(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Register two shapeable scripted providers and record every built seat."""
    created: list[_ScriptedSeat] = []
    shape: dict[str, dict[str, Any]] = {"scripted": {}, "other": {}}

    def factory_for(provider: str) -> Any:
        def factory(identity: str, **kw: Any) -> _ScriptedSeat:
            seat = _ScriptedSeat(identity, **kw, **shape[provider])
            created.append(seat)
            return seat

        return factory

    for provider in shape:
        monkeypatch.setitem(PROVIDERS, provider, factory_for(provider))
    return SimpleNamespace(created=created, shape=shape)


def _exchange_args(opener: str = "scripted", reactor: str = "other", *extra: str) -> Any:
    argv = ["participant", "exchange", "the question", opener, reactor, *extra]
    return build_parser().parse_args(argv)


def _convene_args(*argv_tail: str) -> Any:
    return build_parser().parse_args(["participant", "convene", "the question", *argv_tail])


# --- spec parsing and seat construction --------------------------------------------


def test_parse_spec_without_a_model() -> None:
    assert parse_spec("claude") == ("claude", "")


def test_parse_spec_keeps_colons_inside_the_model() -> None:
    assert parse_spec("ollama:gemma3:1b") == ("ollama", "gemma3:1b")


def test_parse_spec_rejects_an_empty_provider() -> None:
    with pytest.raises(ValueError, match="empty provider"):
        parse_spec(":gemma3:1b")


def test_build_deliberants_numbers_repeated_providers(fabric: SimpleNamespace) -> None:
    seats = build_deliberants(["scripted", "scripted", "scripted"], timeout=1.0)
    assert [seat.identity for seat in seats] == [
        "participant/scripted",
        "participant/scripted-2",
        "participant/scripted-3",
    ]


def test_build_deliberants_refuses_grok() -> None:
    with pytest.raises(ValueError, match="grok turns are disabled"):
        build_deliberants(["grok"], timeout=1.0)


def test_build_deliberants_rejects_an_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown provider 'nonesuch'"):
        build_deliberants(["nonesuch"], timeout=1.0)


def test_refusal_for_gates_only_grok() -> None:
    assert refusal_for("grok") is not None
    assert refusal_for("claude") is None


# --- participant exchange -----------------------------------------------------------


def test_exchange_prints_both_turns_with_markers(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _exchange_args()
    assert args.func is _cmd_exchange
    assert _cmd_exchange(args) == 0
    out = capsys.readouterr().out
    assert out.index("— opener —") < out.index("[participant/scripted] scripted answer")
    assert out.index("— reactor —") < out.index("[participant/other] scripted answer")


def test_exchange_reactor_sees_the_opener_as_fenced_data(fabric: SimpleNamespace) -> None:
    assert _cmd_exchange(_exchange_args()) == 0
    opener, reactor = fabric.created
    assert opener.requests[0].context == ""
    assert REACTION_DIRECTIVE in reactor.requests[0].context
    assert "scripted answer" in reactor.requests[0].context


def test_exchange_refuses_a_bad_spec(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cmd_exchange(_exchange_args("nonesuch", "other")) == 2
    assert "unknown provider" in capsys.readouterr().out


def test_exchange_refuses_grok_on_either_seat(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _cmd_exchange(_exchange_args("scripted", "grok")) == 2
    assert "grok turns are disabled" in capsys.readouterr().out


def test_exchange_reports_an_unavailable_seat(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    fabric.shape["scripted"]["available"] = False
    assert _cmd_exchange(_exchange_args()) == 1
    assert "participant/scripted is unavailable: scripted" in capsys.readouterr().out


def test_exchange_flags_a_degraded_reactor_turn(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    fabric.shape["other"].update(is_error=True, reason="provider exploded", answer="")
    assert _cmd_exchange(_exchange_args()) == 1
    assert "[participant/other] errored: provider exploded" in capsys.readouterr().out


def test_exchange_flags_an_abstaining_reactor_turn(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    fabric.shape["other"].update(abstained=True, reason="declined", answer="")
    assert _cmd_exchange(_exchange_args()) == 1
    assert "[participant/other] abstained: declined" in capsys.readouterr().out


def test_exchange_json_prints_the_transcript_once(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _exchange_args("scripted", "other", "--topic", "t9", "--json")
    assert _cmd_exchange(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["topic_id"] == "t9"
    assert payload["question"] == "the question"
    assert [turn["participant"] for turn in payload["turns"]] == [
        "participant/scripted",
        "participant/other",
    ]


def test_exchange_mints_a_fresh_topic_by_default(fabric: SimpleNamespace) -> None:
    assert _cmd_exchange(_exchange_args()) == 0
    topic = fabric.created[0].requests[0].topic_id
    assert topic.startswith("participant-cli-")


# --- participant convene ------------------------------------------------------------


def test_convene_auto_selects_a_colloquy_for_two_seats(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _convene_args("scripted", "other")
    assert args.func is _cmd_convene
    assert _cmd_convene(args) == 0
    out = capsys.readouterr().out
    for round_no in (1, 2, 3):
        assert f"— round {round_no} —" in out
    assert "mode=colloquy · stopped=completed · turns=6 · cost=$0.0000" in out


def test_convene_honours_an_explicit_mode(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _cmd_convene(_convene_args("scripted", "other", "--mode", "roundtable")) == 0
    out = capsys.readouterr().out
    assert "— round 3 —" not in out
    assert "mode=roundtable · stopped=completed · turns=4 · cost=$0.0000" in out


def test_convene_symposium_synthesises_through_the_moderator(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _convene_args(
        "scripted", "scripted", "other", "--mode", "symposium", "--moderator", "other"
    )
    assert _cmd_convene(args) == 0
    out = capsys.readouterr().out
    assert "— synthesis —" in out
    assert "[participant/other-2] scripted answer" in out
    assert "mode=symposium · stopped=completed · turns=7 · cost=$0.0000" in out


def test_convene_symposium_without_a_moderator_is_refused(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _cmd_convene(_convene_args("scripted", "other", "--mode", "symposium")) == 2
    assert "requires a moderator" in capsys.readouterr().out


def test_convene_refuses_grok_on_the_panel(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cmd_convene(_convene_args("grok", "claude")) == 2
    assert "grok turns are disabled" in capsys.readouterr().out


def test_convene_reports_an_unavailable_moderator(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    fabric.shape["other"]["available"] = False
    args = _convene_args("scripted", "scripted", "--moderator", "other")
    assert _cmd_convene(args) == 1
    assert "participant/other is unavailable: scripted" in capsys.readouterr().out


def test_convene_budget_halt_is_reported_and_nonzero(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    fabric.shape["scripted"]["cost_usd"] = 1.0
    fabric.shape["other"]["cost_usd"] = 1.0
    args = _convene_args("scripted", "other", "--budget-usd", "1.5")
    assert _cmd_convene(args) == 1
    out = capsys.readouterr().out
    assert "— round 2 —" not in out
    assert "mode=colloquy · stopped=budget · turns=2 · cost=$2.0000" in out


def test_convene_flags_a_degraded_panel_turn(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    fabric.shape["other"].update(is_error=True, reason="boom", answer="")
    assert _cmd_convene(_convene_args("scripted", "other", "--mode", "roundtable")) == 1
    assert "[participant/other] errored: boom" in capsys.readouterr().out


def test_convene_json_prints_the_convocation_transcript(
    fabric: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _convene_args(
        "scripted",
        "scripted",
        "other",
        "--mode",
        "symposium",
        "--moderator",
        "other",
        "--topic",
        "t7",
        "--json",
    )
    assert _cmd_convene(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "symposium"
    assert payload["stopped"] == "completed"
    assert [len(round_results) for round_results in payload["rounds"]] == [3, 3]
    assert payload["synthesis"]["participant"] == "participant/other-2"
    assert payload["synthesis"]["topic_id"] == "t7"
    assert payload["total_cost_usd"] == 0.0


def test_convene_numbers_a_moderator_matching_a_panel_provider(
    fabric: SimpleNamespace,
) -> None:
    assert _cmd_convene(_convene_args("scripted", "--moderator", "scripted", "--json")) == 0
    assert [seat.identity for seat in fabric.created] == [
        "participant/scripted",
        "participant/scripted-2",
    ]


def test_convene_shares_the_context_and_timeout_with_every_seat(
    fabric: SimpleNamespace,
) -> None:
    args = _convene_args(
        "scripted", "other", "--context", "be brief", "--timeout", "12.5", "--json"
    )
    assert _cmd_convene(args) == 0
    for seat in fabric.created:
        assert seat._timeout == 12.5
        assert seat.requests[0].context.startswith("be brief")

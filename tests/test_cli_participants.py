# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the participant CLI surface over the Fabric providers

"""Tests for ``synapse participant list`` and ``synapse participant ask``.

The provider registry is exercised for real (constructors and health probes run
against the installed drivers with injected binaries absent or present), while
turns run through a scripted participant injected into the registry so no test
ever drives a real provider CLI or model server.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from synapse_channel import cli_participants
from synapse_channel.cli import build_parser
from synapse_channel.cli_participants import (
    PROVIDERS,
    _cmd_ask,
    _cmd_list,
    build_participant,
)
from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)


class _ScriptedParticipant:
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
    ) -> None:
        self._identity = identity
        self._model = model
        self._timeout = timeout
        self._available = available
        self._answer = answer
        self._is_error = is_error
        self._abstained = abstained
        self._reason = reason
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
            "cost_usd": 0.0,
            "stop_reason": "",
            "model": self._model,
            "input_tokens": 0,
            "output_tokens": 0,
            "rate_limit_utilisation": None,
        }


def _ask_args(**overrides: Any) -> Any:
    parser = build_parser()
    argv = ["participant", "ask", overrides.pop("provider", "scripted"), "hello"]
    for flag, value in overrides.items():
        argv += [f"--{flag.replace('_', '-')}", str(value)]
    return parser.parse_args(argv)


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Register a scripted provider and return its keyword store for shaping."""
    kwargs: dict[str, Any] = {}

    def factory(identity: str, **kw: Any) -> _ScriptedParticipant:
        return _ScriptedParticipant(identity, **kw, **kwargs)

    monkeypatch.setitem(PROVIDERS, "scripted", factory)
    return kwargs


# --- the registry and factory ---------------------------------------------------


def test_registry_names_every_shipped_provider() -> None:
    assert set(PROVIDERS) >= {
        "claude",
        "codex",
        "gemini",
        "grok",
        "kimi",
        "ollama",
        "ollama-api",
    }


def test_build_participant_rejects_an_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown provider 'nonesuch'"):
        build_participant("nonesuch", identity="x", model="", timeout=1.0)


def test_build_participant_requires_a_model_for_ollama_turns() -> None:
    with pytest.raises(ValueError, match="no default model"):
        build_participant("ollama", identity="x", model="", timeout=1.0)


def test_build_participant_probe_skips_the_model_requirement() -> None:
    participant = build_participant("ollama", identity="x", model="", timeout=1.0, probe=True)
    assert participant.identity == "x"


def test_build_participant_constructs_each_registered_provider() -> None:
    for provider in PROVIDERS:
        participant = build_participant(
            provider, identity=f"participant/{provider}", model="m", timeout=1.0
        )
        assert participant.identity == f"participant/{provider}"


# --- participant list ------------------------------------------------------------


def test_list_reports_every_provider_with_grok_caveat(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    args = parser.parse_args(["participant", "list"])
    assert args.func is _cmd_list
    assert _cmd_list(args) == 0
    out = capsys.readouterr().out
    assert f"Participant providers ({len(PROVIDERS)}):" in out
    for provider in PROVIDERS:
        assert f"  {provider} [" in out
    assert "[turns disabled: stream schema unverified]" in out


def test_list_json_carries_the_health_fields(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    args = parser.parse_args(["participant", "list", "--json"])
    assert _cmd_list(args) == 0
    payload = json.loads(capsys.readouterr().out)
    by_provider = {entry["provider"]: entry for entry in payload}
    assert set(by_provider) == set(PROVIDERS)
    assert {"identity", "channel", "available", "detail"} <= set(by_provider["claude"])
    assert "unverified" in by_provider["grok"]["detail"]
    assert "unverified" not in by_provider["gemini"]["detail"]


# --- participant ask -------------------------------------------------------------


def test_ask_prints_the_answer_and_frames_the_request(
    scripted: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    args = _ask_args(context="be brief", topic="t1", model="m1")
    assert args.func is _cmd_ask
    assert _cmd_ask(args) == 0
    assert capsys.readouterr().out.strip() == "scripted answer"


def test_ask_defaults_identity_and_generates_a_topic(
    scripted: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    args = _ask_args()
    args.json = True
    assert _cmd_ask(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["participant"] == "participant/scripted"
    assert result["topic_id"].startswith("participant-cli-")


def test_ask_refuses_grok_while_the_schema_is_unverified(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _ask_args(provider="grok")
    assert _cmd_ask(args) == 2
    assert "grok turns are disabled" in capsys.readouterr().out


def test_gemini_turns_are_enabled_after_real_emitter_capture() -> None:
    assert cli_participants.refusal_for("gemini") is None


def test_gemini_refusal_returns_if_the_schema_flag_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_participants, "GEMINI_SCHEMA_VERIFIED", False)
    assert cli_participants.refusal_for("gemini") == cli_participants._GEMINI_REFUSAL


def test_ask_reports_a_configuration_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    args = _ask_args(provider="ollama")  # no --model
    assert _cmd_ask(args) == 2
    assert "no default model" in capsys.readouterr().out


def test_ask_reports_an_unavailable_provider(
    scripted: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    scripted["available"] = False
    args = _ask_args()
    assert _cmd_ask(args) == 1
    assert "scripted is unavailable: scripted" in capsys.readouterr().out


def test_ask_reports_an_error_turn(
    scripted: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    scripted.update(is_error=True, reason="provider exploded", answer="")
    args = _ask_args()
    assert _cmd_ask(args) == 1
    assert "scripted errored: provider exploded" in capsys.readouterr().out


def test_ask_reports_an_abstention(
    scripted: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    scripted.update(abstained=True, reason="declined", answer="")
    args = _ask_args()
    assert _cmd_ask(args) == 1
    assert "scripted abstained: declined" in capsys.readouterr().out


def test_ask_json_of_an_error_turn_still_exits_nonzero(
    scripted: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    scripted.update(is_error=True, reason="boom", answer="")
    args = _ask_args()
    args.json = True
    assert _cmd_ask(args) == 1
    assert json.loads(capsys.readouterr().out)["is_error"] is True


def test_ask_honours_an_explicit_identity(
    scripted: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    args = _ask_args(identity="team/reviewer-1")
    args.json = True
    assert _cmd_ask(args) == 0
    assert json.loads(capsys.readouterr().out)["participant"] == "team/reviewer-1"


def test_grok_refusal_text_names_the_verification_flag() -> None:
    assert "GROK_SCHEMA_VERIFIED" in cli_participants._GROK_REFUSAL


def test_gemini_refusal_text_names_the_verification_flag() -> None:
    assert "GEMINI_SCHEMA_VERIFIED" in cli_participants._GEMINI_REFUSAL

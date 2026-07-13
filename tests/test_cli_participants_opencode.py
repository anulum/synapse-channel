# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode Participant CLI connection options
"""Tests for CLI-only OpenCode endpoint, auth-file, and directory wiring."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from synapse_channel.cli import build_parser
from synapse_channel.cli_participants import build_participant
from synapse_channel.cli_participants_deliberate import build_deliberants
from synapse_channel.cli_participants_opencode import build_cli_participant
from synapse_channel.participants.headless_opencode import OpenCodeParticipant
from synapse_channel.participants.opencode_api import OpenCodeApiParticipant
from synapse_channel.participants.participant import Participant


def _parse_ask(provider: str, *extra: str) -> argparse.Namespace:
    return build_parser().parse_args(["participant", "ask", provider, "prompt", *extra])


def test_headless_cli_options_select_attach_auth_binary_and_thinking(tmp_path: Path) -> None:
    password = tmp_path / "password"
    args = _parse_ask(
        "opencode",
        "--opencode-directory",
        str(tmp_path),
        "--opencode-endpoint",
        "https://opencode.example",
        "--opencode-username",
        "agent",
        "--opencode-password-file",
        str(password),
        "--opencode-binary",
        "/opt/opencode-1.17.20",
        "--opencode-thinking",
    )
    participant = build_cli_participant(
        "opencode",
        identity="seat/opencode",
        model="provider/model",
        timeout=7,
        args=args,
        fallback=build_participant,
    )

    assert isinstance(participant, OpenCodeParticipant)
    assert participant._directory == tmp_path
    assert participant._attach == "https://opencode.example"
    assert participant._username == "agent"
    assert participant._password_file == str(password)
    assert participant._binary == "/opt/opencode-1.17.20"
    assert participant._thinking is True
    assert participant._timeout == 7


def test_api_cli_options_select_remote_endpoint_and_password_file(tmp_path: Path) -> None:
    password = tmp_path / "password"
    args = _parse_ask(
        "opencode-api",
        "--opencode-directory",
        str(tmp_path),
        "--opencode-endpoint",
        "https://opencode.example/api/",
        "--opencode-username",
        "service-user",
        "--opencode-password-file",
        str(password),
    )
    participant = build_cli_participant(
        "opencode-api",
        identity="seat/opencode-api",
        model="provider/model",
        timeout=11,
        args=args,
        fallback=build_participant,
    )

    assert isinstance(participant, OpenCodeApiParticipant)
    assert participant._directory == tmp_path
    assert participant._endpoint == "https://opencode.example/api"
    assert participant._username == "service-user"
    assert participant._password_file == str(password)
    assert participant._timeout == 11


def test_remote_cleartext_requires_the_explicit_cli_opt_out() -> None:
    refused = _parse_ask("opencode-api", "--opencode-endpoint", "http://opencode.example:4096")
    with pytest.raises(ValueError, match="Remote OpenCode HTTP is refused"):
        build_cli_participant(
            "opencode-api",
            identity="seat/opencode-api",
            model="",
            timeout=1,
            args=refused,
            fallback=build_participant,
        )

    allowed = _parse_ask(
        "opencode-api",
        "--opencode-endpoint",
        "http://opencode.example:4096",
        "--opencode-allow-insecure-http",
    )
    participant = build_cli_participant(
        "opencode-api",
        identity="seat/opencode-api",
        model="",
        timeout=1,
        args=allowed,
        fallback=build_participant,
    )
    assert isinstance(participant, OpenCodeApiParticipant)
    assert participant._endpoint == "http://opencode.example:4096"


def test_deliberation_seats_receive_the_same_opencode_connection_options(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "participant",
            "exchange",
            "question",
            "opencode",
            "opencode-api",
            "--opencode-directory",
            str(tmp_path),
            "--opencode-endpoint",
            "https://opencode.example",
        ]
    )
    headless, api = build_deliberants(["opencode", "opencode-api"], timeout=3, args=args)

    assert isinstance(headless, OpenCodeParticipant)
    assert isinstance(api, OpenCodeApiParticipant)
    assert headless._directory == tmp_path
    assert headless._attach == "https://opencode.example"
    assert api._directory == tmp_path
    assert api._endpoint == "https://opencode.example"


def test_non_opencode_provider_uses_the_stable_fallback_signature() -> None:
    calls: list[tuple[str, str, str, float]] = []

    def fallback(
        provider: str,
        *,
        identity: str,
        model: str,
        timeout: float,
        probe: bool = False,
    ) -> Participant:
        del probe
        calls.append((provider, identity, model, timeout))
        return build_participant("claude", identity=identity, model=model, timeout=timeout)

    participant = build_cli_participant(
        "claude",
        identity="seat/claude",
        model="sonnet",
        timeout=5,
        args=_parse_ask("claude"),
        fallback=fallback,
    )

    assert participant.identity == "seat/claude"
    assert calls == [("claude", "seat/claude", "sonnet", 5)]

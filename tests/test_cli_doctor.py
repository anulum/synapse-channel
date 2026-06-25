# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse doctor` CLI command

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from synapse_channel import cli, cli_doctor


class FakeAgent:
    """Stand-in for SynapseAgent used by the doctor roster-fetch tests."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
        ready: bool = True,
        inbound: list[dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self._ready = ready
        self._inbound = inbound or []

    async def connect(self) -> None:
        for message in self._inbound:
            await self.callback(message)
        await asyncio.Event().wait()  # block until cancelled

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def request_who(self) -> None:
        return None


def _factory(
    holder: list[FakeAgent],
    *,
    ready: bool = True,
    inbound: list[dict[str, Any]] | None = None,
) -> Callable[..., Any]:
    def make(
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
    ) -> FakeAgent:
        agent = FakeAgent(
            name, callback, uri=uri, verbose=verbose, token=token, ready=ready, inbound=inbound
        )
        holder.append(agent)
        return agent

    return make


def _set_project(monkeypatch: pytest.MonkeyPatch, project: str = "demorepo") -> None:
    monkeypatch.setenv("SYN_PROJECT", project)
    monkeypatch.delenv("SYN_IDENTITY", raising=False)


# --- parser ------------------------------------------------------------------


def test_parser_doctor_defaults() -> None:
    args = cli.build_parser().parse_args(["doctor"])
    assert args.func is cli_doctor._cmd_doctor
    assert args.uri.endswith(":8876")
    assert args.project is None
    assert args.id is None
    assert args.send_name is None


def test_parser_doctor_has_token_file_companion() -> None:
    args = cli.build_parser().parse_args(["doctor", "--token-file", "/tmp/tok"])
    assert args.token_file == "/tmp/tok"


def test_parser_doctor_fix_flags() -> None:
    args = cli.build_parser().parse_args(
        ["doctor", "--fix", "--install-user-services", "--identity", "repo/ux"]
    )
    assert args.fix is True
    assert args.install_user_services is True
    assert args.identity == "repo/ux"


# --- _diagnose logic ---------------------------------------------------------


async def test_diagnose_reachable_with_waiter_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_project(monkeypatch)
    snapshot = {"type": "who_snapshot", "online_agents": ["demorepo-rx", "other"]}
    code, lines = await cli_doctor._diagnose(
        uri="ws://localhost:8876",
        project=None,
        agent_id=None,
        token=None,
        agent_factory=_factory([], inbound=[snapshot]),
    )
    text = "\n".join(lines)
    assert "[ok] hub:" in text
    assert "[ok] waiter:" in text
    assert code == 0


async def test_diagnose_reachable_without_waiter_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_project(monkeypatch)
    snapshot = {"type": "who_snapshot", "online_agents": ["other"]}
    code, lines = await cli_doctor._diagnose(
        uri="ws://localhost:8876",
        project=None,
        agent_id=None,
        token=None,
        agent_factory=_factory([], inbound=[snapshot]),
    )
    text = "\n".join(lines)
    assert "no waiter 'demorepo-rx'" in text
    assert code == 0  # a missing waiter warns but does not fail


async def test_diagnose_unreachable_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_project(monkeypatch)
    code, lines = await cli_doctor._diagnose(
        uri="ws://localhost:8876",
        project=None,
        agent_id=None,
        token=None,
        agent_factory=_factory([], ready=False),
    )
    text = "\n".join(lines)
    assert "did not answer" in text
    assert "[warn] waiter:" in text  # unreachable also blocks the waiter check
    assert code == 1


async def test_diagnose_flags_off_loopback_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_project(monkeypatch)
    _, lines = await cli_doctor._diagnose(
        uri="ws://10.0.0.5:8876",
        project=None,
        agent_id=None,
        token=None,
        agent_factory=_factory([], ready=False),
    )
    assert any("off loopback with no token" in line for line in lines)


async def test_diagnose_warns_on_hyphen_send_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_project(monkeypatch)
    snapshot = {"type": "who_snapshot", "online_agents": []}
    _, lines = await cli_doctor._diagnose(
        uri="ws://localhost:8876",
        project=None,
        agent_id=None,
        token=None,
        send_name="demorepo-keeper",
        agent_factory=_factory([], inbound=[snapshot]),
    )
    assert any("hyphen child" in line for line in lines)


# --- dispatch ----------------------------------------------------------------


def test_cmd_doctor_prints_lines_and_returns_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_diagnose(**_: Any) -> tuple[int, list[str]]:
        return (1, ["[FAIL] hub: nope", "synapse doctor: FAILED"])

    monkeypatch.setattr(cli_doctor, "_diagnose", fake_diagnose)
    ns = argparse.Namespace(
        uri="ws://h",
        project=None,
        id=None,
        token=None,
        send_name=None,
        fix=False,
        install_user_services=False,
        start_user_services=False,
        identity=None,
        synapse_bin=None,
    )
    assert cli_doctor._cmd_doctor(ns) == 1
    assert "synapse doctor: FAILED" in capsys.readouterr().out


def test_cmd_doctor_fix_prints_service_commands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_diagnose(**_: Any) -> tuple[int, list[str]]:
        return (0, ["synapse doctor: all clear"])

    _set_project(monkeypatch, "repo")
    monkeypatch.setattr(cli_doctor, "_diagnose", fake_diagnose)
    ns = argparse.Namespace(
        uri="ws://h",
        project=None,
        id=None,
        token=None,
        send_name=None,
        fix=True,
        install_user_services=False,
        start_user_services=False,
        identity="repo/ux",
        synapse_bin="/bin/synapse",
    )
    assert cli_doctor._cmd_doctor(ns) == 0
    out = capsys.readouterr().out
    assert "synapse-arm@.service" in out
    assert "syn arm --project repo" in out


def test_cmd_doctor_installs_user_services(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    async def fake_diagnose(**_: Any) -> tuple[int, list[str]]:
        return (0, ["synapse doctor: all clear"])

    def fake_install(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["wrote synapse-hub.service", "ok: systemctl --user daemon-reload"]

    _set_project(monkeypatch, "repo")
    monkeypatch.setattr(cli_doctor, "_diagnose", fake_diagnose)
    monkeypatch.setattr(cli_doctor, "install_user_services", fake_install)
    ns = argparse.Namespace(
        uri="ws://h",
        project=None,
        id=None,
        token=None,
        send_name=None,
        fix=False,
        install_user_services=True,
        start_user_services=True,
        identity="repo/ux",
        synapse_bin="/bin/synapse",
    )

    assert cli_doctor._cmd_doctor(ns) == 0
    assert captured == {
        "project": "repo",
        "identity": "repo/ux",
        "synapse_bin": "/bin/synapse",
        "start": True,
    }
    assert "systemctl --user daemon-reload" in capsys.readouterr().out


def test_main_routes_to_doctor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_diagnose(**_: Any) -> tuple[int, list[str]]:
        return (0, ["synapse doctor: all clear"])

    monkeypatch.setattr(cli_doctor, "_diagnose", fake_diagnose)
    assert cli.main(["doctor"]) == 0
    assert "all clear" in capsys.readouterr().out

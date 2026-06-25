# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the messaging CLI commands (send/wait/listen)

from __future__ import annotations

import argparse
from typing import Any

import pytest

from cli_messaging_helpers import FakeAgent, _factory
from synapse_channel import cli_arm


async def test_arm_rearms_after_each_wake(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "B", "payload": "wake"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_arm._arm(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        max_wakes=2,
        reconnect_delay=0.0,
        agent_factory=factory,
    )
    assert code == 0
    assert len(holder) == 2
    assert capsys.readouterr().out.count("A: wake") == 2


def test_cmd_arm_derives_rx_name_for_bare_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_arm(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli_arm, "_arm", fake_arm)
    monkeypatch.setattr("synapse_channel.cli_arm.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="B",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
    )
    assert cli_arm._cmd_arm(ns) == 0
    assert captured["name"] == "B-rx"
    assert captured["for_name"] == "B"


def test_cmd_arm_keeps_distinct_connect_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_arm(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli_arm, "_arm", fake_arm)
    monkeypatch.setattr("synapse_channel.cli_arm.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
    )
    assert cli_arm._cmd_arm(ns) == 0
    assert captured["name"] == "B-rx"
    assert captured["for_name"] == "B"

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the async hub client using an injected transport

from __future__ import annotations

import json

import pytest

from client_helpers import FakeWebSocket, _install_connection
from synapse_channel.client.agent import SynapseAgent


async def test_connect_sends_token_on_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    ws = FakeWebSocket([welcome])
    _install_connection(monkeypatch, ws)
    agent = SynapseAgent("A", token="s3cret", verbose=False)
    await agent.connect()
    first = json.loads(ws.sent[0])
    assert first["type"] == "heartbeat"
    assert first["token"] == "s3cret"


async def test_connect_omits_token_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    ws = FakeWebSocket([welcome])
    _install_connection(monkeypatch, ws)
    agent = SynapseAgent("A", verbose=False)
    await agent.connect()
    assert "token" not in json.loads(ws.sent[0])


async def test_registration_includes_takeover_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    ws = FakeWebSocket([welcome])
    _install_connection(monkeypatch, ws)
    agent = SynapseAgent("A-rx", takeover=True, verbose=False)
    await agent.connect()
    first = json.loads(ws.sent[0])
    assert first["type"] == "heartbeat"
    assert first["takeover"] is True


async def test_registration_omits_takeover_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    ws = FakeWebSocket([welcome])
    _install_connection(monkeypatch, ws)
    agent = SynapseAgent("A", verbose=False)
    await agent.connect()
    assert "takeover" not in json.loads(ws.sent[0])

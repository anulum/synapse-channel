# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the async hub client using an injected transport

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from synapse_channel.client import agent as client_module


class FakeWebSocket:
    """Minimal stand-in for a websockets client connection."""

    def __init__(self, incoming: list[str]) -> None:
        self.incoming = incoming
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def __aiter__(self) -> AsyncIterator[str]:
        for message in self.incoming:
            yield message


class FakeConnect:
    """Async context manager mimicking ``websockets.asyncio.client.connect``."""

    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *exc: object) -> None:
        return None


def _install_connection(monkeypatch: pytest.MonkeyPatch, websocket: FakeWebSocket) -> None:
    monkeypatch.setattr(client_module, "connect", lambda uri, **kwargs: FakeConnect(websocket))


# --- construction ------------------------------------------------------------

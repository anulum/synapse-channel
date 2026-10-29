# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real hub that records every decoded inbound frame
"""A real :class:`SynapseHub` that keeps every frame its connections send.

Credential-isolation tests need the frames as the hub reads them after the
WebSocket layer has unmasked them; a TCP capture would see masked client
bytes. The hub serves each connection unchanged through a socket wrapper that
records every frame read with ``recv`` or by iteration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from synapse_channel.core.hub import SynapseHub


class RecordingSocket:
    """Server-side WebSocket that keeps a copy of every frame the hub reads."""

    def __init__(self, inner: Any, frames: list[str | bytes]) -> None:
        self._inner = inner
        self._frames = frames

    async def recv(self, *args: Any, **kwargs: Any) -> str | bytes:
        """Read one frame and record it."""
        raw: str | bytes = await self._inner.recv(*args, **kwargs)
        self._frames.append(raw)
        return raw

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str | bytes]:
        async for raw in self._inner:
            self._frames.append(raw)
            yield raw

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class WireRecordingHub(SynapseHub):
    """Real hub that records the decoded inbound frames of every connection."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.inbound: list[str | bytes] = []

    async def handler(self, websocket: Any) -> None:
        """Serve the connection exactly as the hub does, through a recording socket."""
        await super().handler(RecordingSocket(websocket, self.inbound))

    def inbound_text(self) -> list[str]:
        """Return every recorded frame as text."""
        return [
            frame.decode("utf-8", "replace") if isinstance(frame, bytes) else frame
            for frame in self.inbound
        ]

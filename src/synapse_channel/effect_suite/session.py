# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one connected identity of the suite, asking and waiting for answers
"""Connect one identity to the suite's hub and wait for the frames that answer it.

A wait matches only frames that arrived after the request was sent, and ends
by its deadline. Nothing here polls: every arriving frame wakes the waiters.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.errors import SynapseError

ANSWER_SECONDS = 30.0
"""Seconds a session waits for one answer of the hub."""


class SuiteSessionError(SynapseError, RuntimeError):
    """The hub did not welcome a suite identity, or did not answer it in time."""

    code = "effect_suite_session"


Match = Callable[[dict[str, Any]], bool]


class HubSession:
    """One identity connected to the hub, collecting every frame it is sent.

    Parameters
    ----------
    name : str
        The identity, ``project/agent``.
    uri : str
        The hub's WebSocket address.
    answer_seconds : float, optional
        The deadline of each wait.
    """

    def __init__(self, name: str, uri: str, *, answer_seconds: float = ANSWER_SECONDS) -> None:
        self.name = name
        self.frames: list[dict[str, Any]] = []
        self._arrived = asyncio.Event()
        self._answer_seconds = answer_seconds
        self.agent = SynapseAgent(name, self._collect, uri=uri, verbose=False)
        self._task: asyncio.Task[None] | None = None

    async def _collect(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)
        self._arrived.set()

    async def __aenter__(self) -> HubSession:
        """Connect; refuse when the hub does not welcome this identity in time."""
        self._task = asyncio.create_task(self.agent.connect())
        if await self.agent.wait_until_ready(timeout=self._answer_seconds):
            return self
        await self.__aexit__(None, None, None)
        raise SuiteSessionError(f"the hub did not welcome {self.name}{self._in_time()}")

    async def __aexit__(self, *_exc: object) -> None:
        """Stop the connection task started when the session was entered."""
        self.agent.running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def mark(self) -> int:
        """Return the number of frames seen so far; later waits look only past it."""
        return len(self.frames)

    async def wait_after(self, mark: int, match: Match, *, what: str) -> dict[str, Any]:
        """Return the first frame after ``mark`` that ``match`` accepts.

        Raises
        ------
        SuiteSessionError
            When no such frame arrives within the session's deadline.
        """
        try:
            return await asyncio.wait_for(self._first(mark, match), self._answer_seconds)
        except TimeoutError as exc:
            raise SuiteSessionError(f"{self.name} did not receive {what}{self._in_time()}") from exc

    def _in_time(self) -> str:
        return f" within {self._answer_seconds:g} seconds"

    async def _first(self, mark: int, match: Match) -> dict[str, Any]:
        return (await self._some(mark, match, 1))[0]

    async def _some(self, mark: int, match: Match, count: int) -> list[dict[str, Any]]:
        while True:
            self._arrived.clear()
            found = [frame for frame in self.frames[mark:] if match(frame)]
            if len(found) >= count:
                return found[:count]
            await self._arrived.wait()

    async def wait_count(
        self, mark: int, match: Match, count: int, *, what: str
    ) -> list[dict[str, Any]]:
        """Return the first ``count`` frames after ``mark`` that ``match`` accepts, in order.

        Raises
        ------
        SuiteSessionError
            When fewer arrive within the session's deadline.
        """
        try:
            return await asyncio.wait_for(self._some(mark, match, count), self._answer_seconds)
        except TimeoutError as exc:
            raise SuiteSessionError(
                f"{self.name} did not receive {count} of {what}{self._in_time()}"
            ) from exc

    async def after(
        self, send: Callable[[], Awaitable[None]], match: Match, *, what: str
    ) -> dict[str, Any]:
        """Run ``send`` and return the first later frame that ``match`` accepts."""
        mark = self.mark()
        await send()
        return await self.wait_after(mark, match, what=what)

    async def ask(self, msg_type: str, match: Match, *, what: str, **fields: Any) -> dict[str, Any]:
        """Send one frame and return the first later frame that ``match`` accepts."""
        return await self.after(
            lambda: self.agent.send_message(msg_type, target="System", **fields), match, what=what
        )

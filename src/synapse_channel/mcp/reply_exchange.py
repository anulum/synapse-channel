# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP correlated reply exchange
"""Bounded correlated transport shared by the MCP bridge facade."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

Matcher = Callable[[dict[str, Any]], bool]
"""Predicate that selects the hub reply a pending request is waiting for."""

Sender = Callable[[], Awaitable[None]]
"""Zero-argument coroutine that issues one request on the hub client."""

DEFAULT_REQUEST_TIMEOUT = 5.0
"""Seconds a tool waits for the hub's reply before reporting no response."""


class McpReplyExchange:
    """Own waiter registration, reply correlation and bounded send/reply cleanup."""

    def __init__(self, request_timeout: float) -> None:
        """Initialise the existing bridge deadline and its ordered waiter list."""
        self.request_timeout = request_timeout
        self._waiters: list[tuple[Matcher, asyncio.Future[dict[str, Any]]]] = []

    async def on_message(self, data: dict[str, Any]) -> None:
        """Resolve the first pending request whose matcher accepts ``data``.

        Registered as the hub client's callback, so it sees every inbound message
        and hands each to at most one waiting request.

        Parameters
        ----------
        data : dict[str, Any]
            One decoded inbound message from the hub.
        """
        for waiter in list(self._waiters):
            match, future = waiter
            if not future.done() and match(data):
                future.set_result(data)
                with contextlib.suppress(ValueError):
                    self._waiters.remove(waiter)
                return

    async def _await_reply(self, match: Matcher, send: Sender) -> dict[str, Any] | None:
        """Use the existing bridge deadline for ordinary queries and actions."""
        return await self._await_reply_with_timeout(match, send, self.request_timeout)

    async def _await_reply_with_timeout(
        self, match: Matcher, send: Sender, timeout: float
    ) -> dict[str, Any] | None:
        """Register a matcher, issue ``send``, and return the correlated reply.

        Parameters
        ----------
        match : Matcher
            Predicate selecting the hub reply this request waits for.
        send : Sender
            Coroutine that issues the request on the hub client.
        timeout : float
            Finite deadline covering both the send and correlated reply.

        Returns
        -------
        dict[str, Any] or None
            The matched reply, or ``None`` if none arrived within
            :attr:`request_timeout`.
        """

        async def exchange() -> dict[str, Any]:
            """Include sending in the same finite reply deadline."""
            await send()
            return await future

        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        waiter = (match, future)
        self._waiters.append(waiter)
        try:
            return await asyncio.wait_for(exchange(), timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)

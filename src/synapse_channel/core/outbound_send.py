# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bound outbound socket writes and isolate stalled consumers
"""Bound writes so one unread transport cannot freeze hub fan-out."""

from __future__ import annotations

import asyncio
import math
from contextlib import suppress
from typing import Any

DEFAULT_SEND_TIMEOUT = 5.0
"""Maximum seconds to wait for one outbound write to drain."""
DEFAULT_CLOSE_TIMEOUT = 1.0
"""Maximum graceful close wait before aborting a stalled transport."""


class OutboundSender:
    """Own finite write and close deadlines without persistent background tasks.

    Parameters
    ----------
    send_timeout : float
        Positive finite write deadline, in seconds.
    close_timeout : float
        Positive finite graceful close deadline, in seconds.
    """

    def __init__(
        self,
        *,
        send_timeout: float = DEFAULT_SEND_TIMEOUT,
        close_timeout: float = DEFAULT_CLOSE_TIMEOUT,
    ) -> None:
        """Validate both finite deadlines before any write can start."""
        for value in (send_timeout, close_timeout):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("outbound deadlines must be positive and finite")
        self.send_timeout = send_timeout
        self.close_timeout = close_timeout
        self._replay_holds: dict[int, list[tuple[str, asyncio.Future[None]]]] = {}

    @staticmethod
    def _abort(websocket: Any) -> None:
        """Abort an available transport so pending drain operations can finish."""
        transport = getattr(websocket, "transport", None)
        if transport is not None:
            with suppress(Exception):
                transport.abort()

    async def send(self, websocket: Any, raw: str) -> None:
        """Write one frame or close a stalled peer with code 1013.

        A write timeout first attempts a bounded close, then aborts the transport
        and drains the write task. Caller cancellation also aborts an unfinished
        write; no send task can outlive this call. Transport errors propagate to
        callers, which must not count them as successful delivery.
        """
        hold = self._replay_holds.get(id(websocket))
        if hold is not None:
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            hold.append((raw, future))
            await future
            return
        await self._write(websocket, raw)

    async def send_backlog(self, websocket: Any, raws: list[str]) -> None:
        """Write a replay backlog, then any live frames that arrived during it.

        Live ``send`` calls for this socket wait until every backlog frame has
        been written. If a backlog write fails, those waiting frames are not
        written, so a later sequence cannot pass a gap.

        Parameters
        ----------
        websocket : Any
            Socket that receives the backlog.
        raws : list of str
            Encoded frames in journal order.

        Raises
        ------
        RuntimeError
            If a backlog is already active on ``websocket``.
        TimeoutError
            If one backlog write does not drain in time.
        """
        if not raws:
            return
        key = id(websocket)
        if key in self._replay_holds:
            raise RuntimeError("mailbox replay is already active on this socket")
        held: list[tuple[str, asyncio.Future[None]]] = []
        self._replay_holds[key] = held
        try:
            for raw in raws:
                await self._write(websocket, raw)
        except Exception:
            self._fail_held(key, held)
            raise
        while True:
            if not held:
                # No await between the empty check and clearing the hold, so a
                # live send cannot enqueue into a list that will not be written.
                self._replay_holds.pop(key, None)
                return
            raw, future = held.pop(0)
            try:
                await self._write(websocket, raw)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
                self._fail_held(key, held)
                raise
            if not future.done():
                future.set_result(None)

    def _fail_held(self, key: int, held: list[tuple[str, asyncio.Future[None]]]) -> None:
        """Drop a replay hold and fail every live frame still waiting on it."""
        self._replay_holds.pop(key, None)
        reason = ConnectionError("mailbox replay stopped before this frame was written")
        for _, future in held:
            if not future.done():
                future.set_exception(reason)
        held.clear()

    async def _write(self, websocket: Any, raw: str) -> None:
        """Write one frame or close a stalled peer with code 1013."""
        operation = asyncio.create_task(websocket.send(raw))
        try:
            completed, _ = await asyncio.wait({operation}, timeout=self.send_timeout)
            if completed:
                await operation
                return
            with suppress(Exception):
                await asyncio.wait_for(
                    websocket.close(code=1013, reason="outbound delivery timeout"),
                    timeout=self.close_timeout,
                )
            self._abort(websocket)
            raise TimeoutError("outbound delivery timeout")
        finally:
            if not operation.done():
                self._abort(websocket)
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)

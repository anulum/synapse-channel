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
import weakref
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
        self._replay_holds: weakref.WeakKeyDictionary[
            Any, list[tuple[str, asyncio.Future[None]]]
        ] = weakref.WeakKeyDictionary()

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
        hold = self._replay_holds.get(websocket)
        if hold is not None:
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            hold.append((raw, future))
            try:
                await self._await_held(websocket, future)
            finally:
                if not future.done():
                    future.cancel()
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
        TimeoutError
            If one backlog write or one held live frame does not drain in time.
        """
        if not raws:
            return
        active = self._replay_holds.get(websocket)
        if active is not None:
            await self._enqueue_backlog(websocket, active, raws)
            return
        held: list[tuple[str, asyncio.Future[None]]] = []
        self._replay_holds[websocket] = held
        try:
            for raw in raws:
                await self._write(websocket, raw)
            await self._flush_held(websocket, held)
        finally:
            self._replay_holds.pop(websocket, None)
            self._fail_held(held)

    async def _enqueue_backlog(
        self,
        websocket: Any,
        held: list[tuple[str, asyncio.Future[None]]],
        raws: list[str],
    ) -> None:
        """Queue another backlog behind the one already writing to ``websocket``."""
        loop = asyncio.get_running_loop()
        futures: list[asyncio.Future[None]] = []
        for raw in raws:
            future: asyncio.Future[None] = loop.create_future()
            held.append((raw, future))
            futures.append(future)
        try:
            for future in futures:
                await self._await_held(websocket, future)
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()

    async def _flush_held(
        self, websocket: Any, held: list[tuple[str, asyncio.Future[None]]]
    ) -> None:
        """Write queued frames whose callers are still waiting."""
        while True:
            if not held:
                return
            raw, future = held.pop(0)
            if future.cancelled() or future.done():
                continue
            try:
                await self._write(websocket, raw)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
                raise
            if not future.done():
                future.set_result(None)

    async def _await_held(self, websocket: Any, future: asyncio.Future[None]) -> None:
        """Wait for one held frame, and close a socket that stays blocked."""
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=self.send_timeout)
        except TimeoutError:
            if not future.done():
                future.cancel()
            await self._close_stalled(websocket)
            raise TimeoutError("outbound delivery timeout") from None

    def _fail_held(self, held: list[tuple[str, asyncio.Future[None]]]) -> None:
        """Fail every live frame still waiting after the backlog stops."""
        reason = ConnectionError("mailbox replay stopped before this frame was written")
        for _, future in held:
            if not future.done():
                future.set_exception(reason)
        held.clear()

    async def _close_stalled(self, websocket: Any) -> None:
        """Close one stalled socket with code 1013, then abort it."""
        with suppress(Exception):
            await asyncio.wait_for(
                websocket.close(code=1013, reason="outbound delivery timeout"),
                timeout=self.close_timeout,
            )
        self._abort(websocket)

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

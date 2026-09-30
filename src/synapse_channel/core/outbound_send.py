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

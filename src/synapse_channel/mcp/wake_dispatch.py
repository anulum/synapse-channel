# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded owned webhook attempt lifecycle
"""Own one subprocess through startup, DNS, HTTP, cancellation and final reap."""

from __future__ import annotations

import asyncio
import math
import sys
from dataclasses import dataclass
from typing import Literal, cast

from synapse_channel.mcp.wake_http import MAX_WAKE_INPUT_BYTES, WakeEndpoint, WakeHttpRequest
from synapse_channel.mcp.wake_pointer import WakeAttempt, WakeLedgerError

WAKE_ATTEMPT_SECONDS = 8.0
"""Overall attempt bound, below the ledger's ten-second lease."""


@dataclass(frozen=True)
class WakeHttpResult:
    """Separate retry, permanent refusal and asynchronous HTTP acceptance."""

    outcome: Literal["accepted", "retry", "rejected"]
    http_status: int


def _result(status: int) -> WakeHttpResult:
    """Classify bounded transport status without claiming a business outcome."""
    if 200 <= status <= 299:
        return WakeHttpResult("accepted", status)
    if status == 0 or status in {408, 425, 429} or status >= 500:
        return WakeHttpResult("retry", status)
    return WakeHttpResult("rejected", status)


async def _owned_attempt(raw: bytes) -> int:
    """Keep the input pipe open and always kill/reap the exact spawned child."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "synapse_channel.mcp.wake_http",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=256,
    )
    try:
        writer = cast(asyncio.StreamWriter, process.stdin)
        reader = cast(asyncio.StreamReader, process.stdout)
        writer.write(raw)
        await writer.drain()
        status = int(await reader.readline())
        await process.wait()
        return status if status == 0 or 100 <= status <= 599 else 0
    finally:
        cast(asyncio.StreamWriter, process.stdin).close()
        if process.returncode is None:
            process.kill()
        await process.wait()


async def deliver_pointer(
    endpoint: WakeEndpoint, attempt: WakeAttempt, *, timeout: float = WAKE_ATTEMPT_SECONDS
) -> WakeHttpResult:
    """Deliver immutable ledger bytes with a deadline covering DNS and HTTP.

    Parameters
    ----------
    endpoint : WakeEndpoint
        Fixed operator profile, never taken from an incoming HTTP tool call.
    attempt : WakeAttempt
        Already committed delivery and local attempt fence.
    timeout : float
        Positive finite wall-clock bound of at most eight seconds.

    Returns
    -------
    WakeHttpResult
        Safe transport classification and status. Timeout or unavailable child
        is retryable. Cancellation propagates after the owned child is reaped.

    Raises
    ------
    WakeLedgerError
        For invalid limits or malformed local pointer bytes, before spawning.
    """
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 8:
        raise WakeLedgerError("invalid wake attempt deadline")
    try:
        request = WakeHttpRequest(
            endpoint=endpoint, delivery_id=attempt.delivery_id, body=attempt.body.decode("ascii")
        )
        raw = request.model_dump_json().encode() + b"\n"
        if len(raw) > MAX_WAKE_INPUT_BYTES:
            raise ValueError("wake attempt input exceeds bound")
    except (ValueError, UnicodeError):
        raise WakeLedgerError("invalid wake attempt input") from None
    try:
        status = await asyncio.wait_for(_owned_attempt(raw), timeout)
        return _result(status)
    except (OSError, ValueError, asyncio.TimeoutError):
        return WakeHttpResult("retry", 0)

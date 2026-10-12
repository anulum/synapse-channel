# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — frame decoding and ordered envelope admission
"""Decode inbound frames and charge hosts before inspecting routing fields."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from synapse_channel.core.hub_ingress import HubIngress
from synapse_channel.core.protocol import MessageType, loads_bounded
from synapse_channel.core.ratelimit import RateLimiter


def _decode(raw: str | bytes) -> tuple[dict[str, Any] | None, str | None]:
    """Return an object envelope or its existing protocol refusal."""
    try:
        data = loads_bounded(raw)
    except json.JSONDecodeError:
        return None, "Malformed JSON."
    if not isinstance(data, dict):
        return None, "Malformed frame: expected a JSON object."
    return data, None


async def read_frame(
    raw: str | bytes,
    websocket: Any,
    *,
    host_rate_limiter: RateLimiter | None,
    remote_host: Callable[[Any], str],
    send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
    system: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    """Decode one frame and apply envelope gates in their existing order.

    Parameters
    ----------
    raw : str or bytes
        WebSocket frame received from the client.
    websocket : object
        Socket whose host is charged and receives any refusal.
    host_rate_limiter : RateLimiter or None
        Host quota; object frames, including heartbeats, cost one token.
    remote_host : callable
        Resolve the socket's existing quota principal.
    send_json, system : callable
        The hub's transport and system-message factory.

    Returns
    -------
    dict or None
        Admitted object, or None after sending the existing private error.
        Malformed JSON and non-object frames are refused before host charging;
        mistyped routing fields are refused only after charging the host.
    """
    data, problem = _decode(raw)
    if data is not None:
        if host_rate_limiter is not None and not host_rate_limiter.allow(remote_host(websocket)):
            problem = "Host rate limit exceeded."
        else:
            mistyped = HubIngress.mistyped_text_field(data)
            if mistyped is not None:
                problem = f"Malformed frame: {mistyped!r} must be a string."
    if problem is not None:
        await send_json(websocket, system(problem, msg_type=MessageType.ERROR))
        return None
    return data


async def run_frame_gates(gates: Iterable[Callable[[], Awaitable[bool]]]) -> bool:
    """Run deferred gates in order, stopping at the first terminal decision.

    Parameters
    ----------
    gates : iterable of callable
        Owning hub callbacks; True continues and False ends the frame.
        A terminal gate may have refused or already routed the frame.

    Returns
    -------
    bool
        True only when every gate permits final local dispatch. No later
        callback runs after refusal or cross-domain dispatch.
    """
    for gate in gates:
        if not await gate():
            return False
    return True

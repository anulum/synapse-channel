# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — human-readable classification of hub connection failures
"""Turn a hub close code into an actionable command-line failure message.

When a client cannot complete its registration the command-line verbs used to
print a single generic line — ``Could not reach hub at <uri>`` — regardless of
why. That conflated a genuinely absent hub with one that accepted the socket and
then closed it on purpose: at capacity, during a takeover cooldown, or on a name
conflict. The capacity case in particular read as a total outage even though the
hub was healthy and serving every already-connected agent, which sent operators
hunting for a dead process instead of raising ``--max-clients`` or retrying.

:func:`describe_connect_failure` maps the close code the client recorded onto a
specific, actionable sentence while preserving the original generic line for the
"no socket at all" case.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from websockets.exceptions import ConnectionClosed

from synapse_channel.client.agent_dispatch import MessageCallback, invoke_message_callback
from synapse_channel.core.protocol import MessageType

CAPACITY_CLOSE_CODE = 4013
"""Hub close code for a full connection table — also used for identity refusals.

The hub overloads ``4013``: a connect refused because the table is full
(``reason="hub at capacity"``) and a registration refused because it did not
prove its identity (``reason="identity binding failed"`` / ``"identity pin
mismatch"``). Disambiguate on the reason text, not the code alone.
"""

NAME_CONFLICT_CLOSE_CODE = 4009
"""Hub close code for an online name conflict or a reserved global identity.

The hub overloads ``4009``: a requested name may already be online, or the
global name may be reserved for protocol provenance (``reason="reserved
identity"``). Disambiguate on the reason text so an operator is not told to
retry a name that can never register.
"""


ACCEPTANCE_TIMEOUT_SECONDS = 5.0
"""How long a one-shot client waits for the hub's verdict on its sender."""


class _ObservableConnection(Protocol):
    """The client surface :func:`closed_after_ready` drives and observes."""

    running: bool
    last_close_code: int | None
    last_close_reason: str
    callback: MessageCallback | None

    async def request_who(self, hub: str | None = None) -> None:
        """Ask the hub for its roster as an ordinary sender frame."""


async def closed_after_ready(
    agent: _ObservableConnection, *, timeout: float = ACCEPTANCE_TIMEOUT_SECONDS
) -> bool:
    """Return whether the hub refused a just-ready connection's sender.

    The hub judges a sender only after the welcome. The client's registration
    heartbeat, its first sender frame, carries the identity proof; a name
    conflict (4009), a failed identity proof (4013) or an ownership lease (4016)
    closes the socket once the hub has judged it. A one-shot command that sent
    its operation straight away could report success for a frame the hub then
    refused and never routed, and a fixed grace window only made that less
    likely. So before the operation goes out this sends ``WHO_REQUEST`` and
    waits for the hub's answer. The hub handles one connection's frames in
    order, so that answer comes after its verdict on the heartbeat:

    - ``WHO_SNAPSHOT``: the sender is accepted.
    - A close, or a socket that cannot carry the request: refused. The caller
      describes it from the recorded close code and reason.
    - ``ERROR`` with ``verification_result``: an identity or frame-signature
      refusal. The close that follows is awaited so the caller can name it. If
      the hub keeps the socket open, the refusal text becomes
      ``last_close_reason`` with no close code.
    - ``ERROR`` without ``verification_result``: the sender passed the identity
      gate (for example the hub's ACL refused the roster request). The
      operation's own result still decides.
    - No answer before ``timeout``: refused, never treated as success.

    The frame that answers the request is not passed on to ``agent.callback``;
    every other frame is, and the callback is restored on return.

    Parameters
    ----------
    agent : _ObservableConnection
        The just-connected client: its ``running`` flag, recorded close code and
        reason, message ``callback`` and ``request_who``.
    timeout : float, optional
        Seconds to wait for the hub's answer.

    Returns
    -------
    bool
        ``False`` once the hub accepted the sender, ``True`` otherwise.
    """
    answer: list[dict[str, Any]] = []
    forward = agent.callback

    async def observe(data: dict[str, Any]) -> None:
        if not answer and data.get("type") in (MessageType.WHO_SNAPSHOT, MessageType.ERROR):
            answer.append(data)
        elif forward is not None:
            await invoke_message_callback(forward, data)

    agent.callback = observe
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    try:
        try:
            await agent.request_who()
        except (ConnectionClosed, OSError):
            return True
        while loop.time() < deadline:
            if not agent.running or agent.last_close_code is not None:
                return True
            if answer and "verification_result" not in answer[0]:
                return False
            await asyncio.sleep(0.02)
        if answer:
            agent.last_close_reason = str(answer[0].get("payload") or "frame refused")
        return True
    finally:
        agent.callback = forward


SUPERSEDED_CLOSE_CODE = 4010
"""Hub close code for a superseded socket — also used for authentication refusal.

The hub overloads ``4010``: a socket is closed with it both when a takeover
supersedes it (``reason="superseded"``) and when a secured hub rejects
authentication (``reason="auth denied"`` / ``"auth required"``). Disambiguate on
the reason text, not the code alone.
"""

AUTH_TIMEOUT_CLOSE_CODE = 4012
"""Hub close code emitted when a secured hub's authentication window expires."""

TAKEOVER_COOLDOWN_CLOSE_CODE = 4014
"""Hub close code for a takeover cooldown — also used for the unauth-socket cap.

The hub overloads ``4014``: a takeover refused during its cooldown
(``reason="takeover cooldown"``) and too many unauthenticated sockets from one
host (``reason="too many unauthenticated connections"``). Disambiguate on reason.
"""

PER_HOST_CAP_CLOSE_CODE = 4015
"""Hub close code emitted when a host exceeds its per-host connection cap."""

NAME_OWNED_CLOSE_CODE = 4016
"""Hub close code emitted when a leased name is claimed without its owner_lease token.

Distinct from the ``4009`` name conflict: the refusal stands whether the
name's owner is currently connected or not, and a ``takeover`` flag does not
override it — only presenting the matching lease token does.
"""


def is_superseded_close(code: int | None, reason: str) -> bool:
    """Return whether a close means a takeover displaced this connection.

    ``4010`` is overloaded (it also carries authentication refusals), so the
    reason text decides: only the takeover eviction reads ``"superseded"``. A
    displaced waiter must *yield* — a newer connection legitimately owns the
    name — rather than reconnect with a takeover of its own and fight the new
    holder for the identity indefinitely.
    """
    return code == SUPERSEDED_CLOSE_CODE and "auth" not in reason.lower()


def is_identity_refused_close(code: int | None, reason: str) -> bool:
    """Return whether a close means the hub refused this side's identity proof.

    ``4013`` is overloaded with the capacity refusal, so the reason text
    decides. For a waiter this is a *yield* verdict: the name is pinned to (or
    requires) an identity key this process does not hold, and retrying with
    the same credentials can never succeed.
    """
    return code == CAPACITY_CLOSE_CODE and "identity" in reason.lower()


def is_name_owned_close(code: int | None, reason: str) -> bool:
    """Return whether a close means an ownership lease refused this claim.

    ``4016`` is not overloaded, but the reason text is still checked so a
    proxy or future reuse cannot silently widen the match. For a waiter this
    is a *yield* verdict, like a refused takeover: another identity owns the
    name and retrying without the lease token can never succeed.
    """
    return code == NAME_OWNED_CLOSE_CODE and "owned" in reason.lower()


def is_takeover_refused_close(code: int | None, reason: str) -> bool:
    """Return whether a close means the hub refused this side's takeover.

    ``4014`` is overloaded with the unauthenticated-socket cap, so the reason
    text decides. A refused takeover means another live connection holds the
    name and the hub is protecting it (cooldown or oscillation quarantine) —
    for a re-arming waiter that is the same verdict as superseded: yield.
    """
    return code == TAKEOVER_COOLDOWN_CLOSE_CODE and "takeover" in reason.lower()


def _guidance_for(code: int, reason: str) -> str | None:
    """Return actionable guidance for a hub close, disambiguating reused codes.

    Parameters
    ----------
    code : int
        The WebSocket close code the hub sent.
    reason : str
        The hub-supplied reason text, used to split the overloaded ``4010`` and
        ``4014`` codes into their distinct authentication and takeover meanings.

    Returns
    -------
    str or None
        Guidance for a recognised deliberate close, or ``None`` for an
        unrecognised code.
    """
    reason_l = reason.lower()
    if code == NAME_CONFLICT_CLOSE_CODE:
        if "reserved" in reason_l:
            return (
                "name is reserved for hub protocol provenance and cannot be registered. "
                "Choose a non-reserved global --name or a project-scoped identity"
            )
        return "name already online from another session. Reconnect with a unique --name"
    if code == CAPACITY_CLOSE_CODE:
        if "identity" in reason_l:
            return (
                "identity proof refused: the name is bound to an identity key this "
                "process did not prove. Connect from the machine holding that key, "
                "or follow the recovery path in the hub error message"
            )
        return (
            "hub at capacity: too many connections are open. Retry shortly, reap "
            "stale waiters, or restart the hub with a higher --max-clients"
        )
    if code == SUPERSEDED_CLOSE_CODE:
        if "auth" in reason_l:
            return (
                "authentication rejected by the secured hub. Pass a valid --token (or --token-file)"
            )
        return "connection superseded by a takeover from another session holding this name"
    if code == AUTH_TIMEOUT_CLOSE_CODE:
        return (
            "authentication timed out: the secured hub closed the socket before a "
            "valid token arrived. Authenticate sooner"
        )
    if code == TAKEOVER_COOLDOWN_CLOSE_CODE:
        if "unauth" in reason_l or "too many" in reason_l:
            return (
                "too many unauthenticated connections from this host. Authenticate "
                "sooner or retry once earlier sockets clear"
            )
        return "takeover refused during the cooldown window. Wait for the cooldown, then retry"
    if code == PER_HOST_CAP_CLOSE_CODE:
        return (
            "per-host connection cap reached. Close other sockets from this host or "
            "raise --max-connections-per-host"
        )
    if code == NAME_OWNED_CLOSE_CODE:
        return (
            "name is protected by an ownership lease held by another identity. "
            "Reconnect presenting its owner_lease token, wait for the offline "
            "lease window to lapse, or choose a unique --name"
        )
    return None


def describe_connect_failure(
    name: str,
    uri: str,
    *,
    close_code: int | None = None,
    close_reason: str = "",
) -> str:
    """Return a command-line message explaining why a hub connection failed.

    Parameters
    ----------
    name : str
        Client identity used as the bracketed message prefix.
    uri : str
        Hub URI shown when the failure is an absent or unreachable hub.
    close_code : int or None, optional
        WebSocket close code the client recorded for the most recent connection,
        or ``None`` when the socket never connected (a refused or absent hub) or
        the hub refused the sender without closing it.
    close_reason : str, optional
        Close reason text the hub supplied, appended verbatim when it adds detail
        beyond the recognised guidance. With no close code it is the refusal
        :func:`closed_after_ready` recorded for an open socket.

    Returns
    -------
    str
        A specific, actionable line for a recognised deliberate close, the
        recorded refusal for an open socket, otherwise the generic
        ``Could not reach hub`` line for an absent or silent hub.
    """
    if close_code is None:
        if close_reason:
            return f"[{name}] Hub refused this sender without closing: {close_reason}."
        return f"[{name}] Could not reach hub at {uri}."
    guidance = _guidance_for(close_code, close_reason)
    if guidance is None:
        detail = f": {close_reason}" if close_reason else ""
        return f"[{name}] Hub closed the connection (code {close_code}){detail}."
    reason = close_reason.strip()
    suffix = f" (hub said: {reason})" if reason and reason not in guidance else ""
    return f"[{name}] {guidance} (code {close_code}){suffix}."


def explain_silent_outcome(
    name: str,
    uri: str,
    *,
    close_code: int | None,
    close_reason: str,
    fallback: str,
) -> str:
    """Explain a request that produced no application reply.

    A claim or lock waits for a granted/denied reply and otherwise reports a flat
    "no response"/"timed out". When the hub closed the socket — because the name
    was already online, the hub was full, or a takeover superseded it — that
    close code is the real reason the reply never came. Surface it instead of the
    generic fallback so the caller sees the actionable cause.

    Parameters
    ----------
    name, uri : str
        Client identity and hub URI, forwarded to :func:`describe_connect_failure`.
    close_code : int or None
        Close code the client recorded, or ``None`` when the socket stayed open
        (a genuine no-reply that the fallback already describes).
    close_reason : str
        Close reason text the hub supplied.
    fallback : str
        Message to return when no close code was recorded.

    Returns
    -------
    str
        The classified connection-close message, or ``fallback`` when the socket
        was never closed with a code.
    """
    if close_code is None:
        return fallback
    return describe_connect_failure(name, uri, close_code=close_code, close_reason=close_reason)

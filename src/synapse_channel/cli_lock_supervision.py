# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — long-running command lease supervision
"""Renew only the original live lease and stop the owned command on loss or uncertainty."""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.cli_lock_release import LockRelease, display_hub_uri
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.lease_ownership import claim_continues
from synapse_channel.client.owner_registration import close_owner, register_owner
from synapse_channel.connect_failures import describe_connect_failure
from synapse_channel.core.numeric_coercion import safe_float
from synapse_channel.core.protocol import SENDER_HUB, MessageType

STOP_ALLOWANCE = 5.0


def claim_verdict_matches(
    data: dict[str, Any],
    *,
    hub_id: str,
    task_id: str,
    request_id: str,
) -> bool:
    """Bind an acquisition verdict to this hub, task and fresh request."""
    return bool(request_id) and (
        data.get("sender") == SENDER_HUB
        and data.get("hub_id") == hub_id
        and data.get("task_id") == task_id
        and data.get("claim_request_id") == request_id
    )


async def connect_lock_owner(
    agent: SynapseAgent,
    profile: LockRelease,
    welcome: dict[str, Any],
) -> tuple[asyncio.Task[None], str] | None:
    """Bind a fresh profile and negotiate this connection's supervision support."""
    welcome.clear()
    listener = await register_owner(agent, profile.ready_timeout)
    if listener is None:
        print(
            describe_connect_failure(
                profile.name,
                display_hub_uri(profile.uri),
                close_code=agent.last_close_code,
                close_reason=agent.last_close_reason,
            )
        )
        return None
    session = welcome.get("lock_renewal_session")
    if isinstance(session, str) and len(session) == 32:
        return listener, session
    await close_owner(agent, listener)
    print("lock: hub does not support fenced renewal; no command started", file=sys.stderr)
    return None


def lease_duration(data: dict[str, Any]) -> float | None:
    """Return a finite lease duration admitting the command's termination allowance."""
    value = data.get("lease_duration_seconds")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    duration = safe_float(value, default=None, allow_bool=False)
    if duration is not None and duration > STOP_ALLOWANCE:
        return duration
    return None


def _renewal_reply(
    data: dict[str, Any],
    profile: LockRelease,
    hub_id: str,
    renewal_id: str,
) -> bool:
    """Match only an addressed refusal or this owner's exact renewal grant."""
    return (
        data.get("sender") == SENDER_HUB
        and data.get("hub_id") == hub_id
        and data.get("task_id") == profile.task_id
        and data.get("renewal_id") == renewal_id
        and (
            (data.get("type") == MessageType.CLAIM_GRANTED and data.get("owner") == profile.name)
            or (data.get("type") == MessageType.CLAIM_DENIED and data.get("target") == profile.name)
        )
    )


async def renew_lock(
    profile: LockRelease, *, hub_id: str, session: str
) -> tuple[int, float | None]:
    """Return a correlated renewal verdict without ever issuing a fresh acquisition.

    The same authenticated name is bound briefly, without takeover. A restarted
    hub has a different welcome session and is refused before mutation. Unknown
    replies are never retried; the supervisor stops its command instead.
    """
    welcome: dict[str, Any] = {}

    async def collect(data: dict[str, Any]) -> None:
        """Record the welcome and deliver only this exchange's correlated reply."""
        if data.get("type") == MessageType.WELCOME:
            welcome.update(data)
        await profile.collect(data)

    agent = profile.agent_factory(
        profile.name, collect, uri=profile.uri, verbose=False, token=profile.token
    )
    listener = await profile.register_owner(agent)
    if listener is None:
        return 3, None
    try:
        if agent.hub_id != hub_id or welcome.get("lock_renewal_session") != session:
            return 3, None
        renewal_id = uuid.uuid4().hex
        reply = await profile.exchange(
            lambda data: _renewal_reply(data, profile, hub_id, renewal_id),
            lambda: agent.send_message(
                MessageType.CLAIM,
                target="System",
                payload=profile.task_id,
                task_id=profile.task_id,
                renew_epoch=profile.initial_epoch,
                renewal_id=renewal_id,
                idem_key=renewal_id,
                paths=list(profile.renewal_paths),
            ),
        )
        if reply is None:
            return 3, None
        if reply.get("type") == MessageType.CLAIM_DENIED:
            return 1, None
        duration = lease_duration(reply)
        if not claim_continues(reply, profile.name, profile.initial_epoch) or duration is None:
            return 3, None
        return 0, duration
    finally:
        agent.running = False
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)


async def supervise_locked_command(
    command: list[str],
    runner: Callable[[list[str]], Awaitable[int]],
    profile: LockRelease,
    *,
    hub_id: str,
    session: str,
    duration: float,
    acquired_at: float,
) -> int:
    """Join the child before cleanup, preserving child failures and reporting lease loss.

    Parameters
    ----------
    command, runner
        Owned executable arguments and cancellation-safe subprocess runner.
    profile
        Original authenticated identity and pinned lease generation.
    hub_id, session
        Actual acquisition hub and advertised renewal session.
    duration, acquired_at
        Confirmed lease duration and conservative monotonic request start.

    Returns
    -------
    int
        Normal child status, one for explicit ownership loss, three for uncertainty.
    """
    loop = asyncio.get_running_loop()

    async def monitor() -> int:
        """Renew before expiry without extending authority on an uncertain response."""
        allowance = duration
        deadline = acquired_at + allowance - STOP_ALLOWANCE
        while True:
            await asyncio.sleep(min(5.0, allowance / 3.0, max(0.0, deadline - loop.time())))
            started = loop.time()
            remaining = deadline - started
            if remaining <= 0:
                return 3
            try:
                code, renewed = await asyncio.wait_for(
                    renew_lock(profile, hub_id=hub_id, session=session), remaining
                )
            except (asyncio.TimeoutError, OSError):
                return 3
            if code:
                return code
            assert renewed is not None
            allowance = renewed
            deadline = started + allowance - STOP_ALLOWANCE

    if acquired_at + duration - STOP_ALLOWANCE <= loop.time():
        print(
            "lock: acquired lease has insufficient remaining lifetime; no command started",
            file=sys.stderr,
        )
        return 3
    child = asyncio.ensure_future(runner(command))
    watcher = asyncio.create_task(monitor())
    try:
        done, _ = await asyncio.wait((child, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            code = watcher.result()
            print(
                f"lock: lease {'lost' if code == 1 else 'unknown'} for {profile.task_id!r}; "
                "stopping the owned command",
                file=sys.stderr,
            )
            return child.result() or code if child.done() else code
        return child.result()
    finally:
        child.cancel()
        watcher.cancel()
        cleanup = asyncio.gather(child, watcher, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue


async def finish_lock(
    agent: SynapseAgent,
    listener: asyncio.Task[None] | None,
    release: LockRelease | None,
    child_code: int | None,
) -> int:
    """Join socket and bounded release cleanup despite repeated caller cancellation."""
    cleanup_code = 0

    async def teardown() -> None:
        """Join the original socket before a fresh profile settles the retained intent."""
        nonlocal cleanup_code
        await close_owner(agent, listener)
        if release is not None:
            cleanup_code = await release.run()

    interrupted = False
    while True:
        cleanup = asyncio.create_task(teardown())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        if cleanup.cancelled():
            interrupted = True
            continue
        cleanup.result()
        break
    if cleanup_code and release is not None:
        print(release.diagnostic(cleanup_code, child_code), file=sys.stderr)
    if interrupted:
        raise asyncio.CancelledError
    return cleanup_code

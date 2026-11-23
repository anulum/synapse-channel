# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the restore's hub grace: its own connection proves the hub stayed up
"""The hub grace a restore waits out before it starts a seat.

A hub that has just started has not yet seen the seats that will reconnect to
it, so a seat holder could take a name whose live owner is about to come back.
The restore therefore holds its own connection, as ``<host>-restore-planner``
and never with a takeover, for the whole run:

* the first start waits until that connection has been up, without a break,
  for :data:`RESTORE_GRACE_SECONDS`; a hub cannot restart inside a connection
  it is still serving;
* before every start, the hub must answer a roster request on that connection
  within :data:`ANSWER_SECONDS`; a hub that keeps the socket open without
  serving it is a drop;
* a drop restarts the grace from zero, and the reconnect's welcome must name
  the same ``hub_id`` as the first, or the run stops;
* a planner name that a live connection holds means another restore runs, and
  a hub that cannot be reached stops the run.

The hub's ``/health``, where it is served, is only a shortcut for the first
wait: it counts when it reports ``uptime_seconds`` of at least the grace and the
same ``hub_id`` as the welcome. The connection is held and checked all the same.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from synapse_channel.client.agent_dispatch import MessageCallback
from synapse_channel.connect_failures import closed_after_ready, is_name_conflict_close
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.wake_capability import WAKE_PASSIVE
from synapse_channel.seat_hold import HUB_UNAVAILABLE_EXIT_CODE, SEAT_LIVE_EXIT_CODE

RESTORE_GRACE_SECONDS = 45.0
"""How long the planner's connection must have been up before the first start."""

ANSWER_SECONDS = 5.0
"""How long the hub has to answer before a start, and to welcome a connection."""

GRACE_WAIT_LIMIT_SECONDS = 300.0
"""How long one wait for the grace may take before the run stops."""

HEALTH_TIMEOUT_SECONDS = 2.0
"""How long the ``/health`` shortcut may take; its absence only means no shortcut."""

HealthReader = Callable[[str], tuple[str, float] | None]
"""Returns the hub's ``hub_id`` and ``uptime_seconds`` from ``/health``, or ``None``."""


class Connection(Protocol):
    """The client surface the grace drives: one connection, its welcome and its verdicts."""

    running: bool
    last_close_code: int | None
    last_close_reason: str
    callback: MessageCallback | None

    @property
    def hub_id(self) -> str:
        """Hub identity from the latest welcome."""
        raise NotImplementedError

    async def connect(self) -> None:
        """Run one connection until it closes."""

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Wait for the hub's welcome."""

    async def request_who(self, hub: str | None = None) -> None:
        """Ask the hub for its roster."""


ConnectionFactory = Callable[..., Connection]
"""Builds the planner's client; ``SynapseAgent`` in production."""


@dataclass(frozen=True)
class _Held:
    """One open connection and the task that runs it."""

    agent: Connection
    task: asyncio.Task[None]


class HubGraceError(SynapseError):
    """The run must stop before the next start; ``exit_code`` is the run's exit code."""

    code = "restore_hub_grace"

    def __init__(self, exit_code: int, reason: str) -> None:
        super().__init__(reason)
        self.exit_code = exit_code


def health_reading(uri: str, timeout: float = HEALTH_TIMEOUT_SECONDS) -> tuple[str, float] | None:
    """Return the ``hub_id`` and ``uptime_seconds`` the hub's ``/health`` reports, if it does.

    Only a ``ws`` or ``wss`` URI is read, over ``http`` or ``https`` on the same
    host and port; any failure, including a hub that does not serve the
    endpoint, is ``None``.
    """
    parts = urllib.parse.urlsplit(uri)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme)
    if scheme is None or not parts.netloc:
        return None
    url = urllib.parse.urlunsplit((scheme, parts.netloc, "/health", "", ""))
    try:
        # The scheme is fixed to http or https above; nothing else is opened.
        with urllib.request.urlopen(url, timeout=timeout) as answer:  # nosec B310
            document = json.loads(answer.read(65536))
    except (OSError, ValueError):
        return None
    hub_id = document.get("hub_id") if isinstance(document, dict) else None
    uptime = document.get("uptime_seconds") if isinstance(document, dict) else None
    if not isinstance(hub_id, str) or isinstance(uptime, bool):
        return None
    return (hub_id, float(uptime)) if isinstance(uptime, int | float) else None


class HubGrace:
    """The planner's one connection to the hub, and the grace measured on it."""

    def __init__(
        self,
        name: str,
        *,
        uri: str,
        token: str | None,
        factory: ConnectionFactory,
        health: HealthReader | None = health_reading,
        clock: Callable[[], float] = time.monotonic,
        grace: float = RESTORE_GRACE_SECONDS,
        answer: float = ANSWER_SECONDS,
        limit: float = GRACE_WAIT_LIMIT_SECONDS,
    ) -> None:
        self.name = name
        self._uri, self._token, self._factory, self._health = uri, token, factory, health
        self._clock, self._grace, self._answer, self._limit = clock, grace, answer, limit
        self._held: _Held | None = None
        self.hub_id: str | None = None
        self._up_since = 0.0

    async def ready(self) -> None:
        """Return once a start may go: the hub answered now and has been up for the grace.

        Raises
        ------
        HubGraceError
            When the hub cannot be reached, changed, refused the planner's
            name, or did not stay up within :data:`GRACE_WAIT_LIMIT_SECONDS`.
        """
        deadline = self._clock() + self._limit
        while True:
            held = self._held
            if held is None or held.task.done():
                held = await self._connect()
            elif await closed_after_ready(held.agent, timeout=self._answer):
                await self.close()  # no answer in time: a drop, and the grace starts again
                continue
            remaining = self._grace - (self._clock() - self._up_since)
            if remaining <= 0:
                return
            if self._clock() + remaining > deadline:
                raise HubGraceError(
                    HUB_UNAVAILABLE_EXIT_CODE, "the hub did not stay up long enough"
                )
            await asyncio.wait({held.task}, timeout=remaining)

    async def _connect(self) -> _Held:
        """Open the connection; the grace starts at its welcome."""
        await self.close()
        agent = self._factory(
            self.name,
            None,
            uri=self._uri,
            verbose=False,
            token=self._token,
            takeover=False,
            wake_capability=WAKE_PASSIVE,
        )
        held = self._held = _Held(agent, asyncio.create_task(agent.connect()))
        if not await agent.wait_until_ready(timeout=self._answer):
            await self.close()
            raise HubGraceError(HUB_UNAVAILABLE_EXIT_CODE, "the hub cannot be reached")
        if await closed_after_ready(agent, timeout=self._answer):
            live = is_name_conflict_close(agent.last_close_code, agent.last_close_reason)
            await self.close()
            if live:
                raise HubGraceError(
                    SEAT_LIVE_EXIT_CODE, f"another restore holds {self.name}; this one stops"
                )
            raise HubGraceError(HUB_UNAVAILABLE_EXIT_CODE, "the hub refused the restore planner")
        first = self.hub_id is None
        if not first and agent.hub_id != self.hub_id:
            await self.close()
            raise HubGraceError(HUB_UNAVAILABLE_EXIT_CODE, f"the hub changed ({agent.hub_id})")
        self.hub_id, self._up_since = agent.hub_id, self._clock()
        if first and await self._shortcut(agent.hub_id):
            self._up_since -= self._grace
        return held

    async def _shortcut(self, welcome: str) -> bool:
        """Return whether ``/health`` proves the first wait unneeded for this same hub.

        The read is a blocking HTTP request, so it runs off the event loop that
        serves the held connection.
        """
        if self._health is None:
            return False
        reading = await asyncio.to_thread(self._health, self._uri)
        return reading is not None and reading[0] == welcome and reading[1] >= self._grace

    async def close(self) -> None:
        """Close the connection, if one is open."""
        held, self._held = self._held, None
        if held is not None:
            held.agent.running = False
            held.task.cancel()
            await asyncio.gather(held.task, return_exceptions=True)

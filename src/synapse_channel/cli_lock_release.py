# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded lock-release confirmation
"""Settle one lock teardown without replaying an uncertain release mutation."""

from __future__ import annotations

import asyncio
import logging
import math
import shlex
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.exceptions import ConnectionClosed

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import SENDER_HUB, MessageType
from synapse_channel.core.release_confirmation import ReleaseIntent

AgentFactory = Callable[..., SynapseAgent]
logger = logging.getLogger("synapse.lock")


def display_hub_uri(uri: str) -> str:
    """Return a failure-display address without URI authentication material."""
    return "<configured hub>" if any(marker in uri for marker in ("@", "?", "#")) else uri


def valid_lock_timeouts(wait: float, ready: float, reply: float) -> bool:
    """Require finite acquisition and positive bounded cleanup deadlines."""
    return (
        math.isfinite(wait)
        and wait >= 0
        and all(math.isfinite(value) and 0 < value <= 300 for value in (ready, reply))
    )


class LockRelease:
    """Retain the identity of a single release across interrupted cleanup.

    Parameters
    ----------
    uri, name, task_id : str
        Hub, authenticated owner and mutex being released.
    initial_epoch : int or None
        Actual original grant, used only when a fresh client has no stored epoch.
    token : str or None
        Existing connection credential; never included in recovery output.
    agent_factory : callable
        Construct the same client profile used to acquire the mutex.
    ready_timeout, reply_timeout : float
        Bounds for connection readiness and each complete protocol exchange.
    """

    def __init__(
        self,
        *,
        uri: str,
        name: str,
        task_id: str,
        initial_epoch: int | None,
        token: str | None,
        agent_factory: AgentFactory,
        ready_timeout: float,
        reply_timeout: float,
    ) -> None:
        self.uri, self.name, self.task_id = uri, name, task_id
        self.initial_epoch, self.token = initial_epoch, token
        self.agent_factory = agent_factory
        self.ready_timeout, self.reply_timeout = ready_timeout, reply_timeout
        self.intent: ReleaseIntent | None = None
        self.pending: (
            tuple[Callable[[dict[str, Any]], bool], asyncio.Future[dict[str, Any]]] | None
        ) = None

    async def collect(self, data: dict[str, Any]) -> None:
        """Accept only the currently registered, correlated protocol response."""
        if self.pending is not None and not self.pending[1].done() and self.pending[0](data):
            self.pending[1].set_result(data)

    async def exchange(
        self,
        match: Callable[[dict[str, Any]], bool],
        send: Callable[[], Awaitable[None]],
    ) -> dict[str, Any] | None:
        """Bound send and reply together, registering the callback before dispatch."""
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending = match, future

        async def complete() -> dict[str, Any]:
            """Send once and await the registered response."""
            await send()
            return await future

        try:
            return await asyncio.wait_for(complete(), self.reply_timeout)
        except (TimeoutError, ConnectionClosed, OSError):
            return None
        finally:
            self.pending = None

    def verdict(self, agent: SynapseAgent, intent: ReleaseIntent, data: dict[str, Any]) -> bool:
        """Match a keyed grant or explicit refusal from this connected hub."""
        if data.get("sender") != SENDER_HUB or data.get("hub_id") != agent.hub_id:
            return False
        if data.get("type") == MessageType.RELEASE_GRANTED:
            return intent.matching_receipt(data) is not None
        return (
            data.get("type") in (MessageType.ERROR, MessageType.RELEASE_DENIED)
            and data.get("target") == self.name
            and data.get("task_id") == self.task_id
            and data.get("release_operation_id") == intent.operation_id
        )

    async def confirm(self, agent: SynapseAgent, intent: ReleaseIntent) -> bool:
        """Read the exact durable operation; lease absence is never confirmation."""
        request_id = uuid.uuid4().hex
        reply = await self.exchange(
            lambda data: (
                data.get("sender") == SENDER_HUB
                and data.get("hub_id") == agent.hub_id
                and data.get("type") == MessageType.STATE_SNAPSHOT
                and data.get("target") == self.name
                and data.get("request_id") == request_id
            ),
            lambda: agent.request_release_confirmation(
                self.task_id,
                intent.operation_id,
                intent.request_digest,
                request_id,
            ),
        )
        projection = reply.get("release_confirmation") if reply is not None else None
        return (
            isinstance(projection, dict)
            and projection.get("status") == "confirmed"
            and intent.matching_receipt(projection) is not None
        )

    def prepare_release(self, agent: SynapseAgent) -> tuple[dict[str, Any], ReleaseIntent]:
        """Bind and retain the fresh profile's epoch before entering the transport."""
        request = agent.prepare_release(self.task_id, idem_key=uuid.uuid4().hex)
        if "epoch" not in request and self.initial_epoch is not None:
            request = agent.prepare_release(
                self.task_id,
                epoch=self.initial_epoch,
                idem_key=request["idem_key"],
            )
        self.intent = ReleaseIntent.from_request(request)
        return request, self.intent

    async def send_release(
        self, agent: SynapseAgent, request: dict[str, Any], intent: ReleaseIntent
    ) -> dict[str, Any] | None:
        """Dispatch this prepared mutation once and await only its own verdict."""

        async def send() -> None:
            """Retain uncertainty before entering the transport's send boundary."""
            await agent.send_message(
                MessageType.RELEASE,
                target=request["target"],
                payload=request["payload"],
                **{
                    key: value
                    for key, value in request.items()
                    if key not in {"sender", "type", "target", "payload", "timestamp"}
                },
            )

        return await self.exchange(lambda data: self.verdict(agent, intent, data), send)

    async def run(self) -> int:
        """Return zero for confirmation, one for refusal, or three for uncertainty."""
        try:
            return await self.settle()
        except Exception:
            logger.debug(
                "best-effort release of %r failed on teardown", self.task_id, exc_info=True
            )
            return 3

    async def settle(self) -> int:
        """Construct the fresh profile and join its listener on every connected exit."""
        agent = self.agent_factory(
            self.name, self.collect, uri=self.uri, verbose=False, token=self.token
        )
        listener = asyncio.create_task(agent.connect())
        try:
            if not await agent.wait_until_ready(self.ready_timeout):
                logger.debug("could not reconnect to release the held claim")
                return 3
            intent = self.intent
            if intent is None:
                request, intent = self.prepare_release(agent)
                reply = await self.send_release(agent, request, intent)
                if reply is not None:
                    return 0 if reply.get("type") == MessageType.RELEASE_GRANTED else 1
            return 0 if await self.confirm(agent, intent) else 3
        finally:
            agent.running = False
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)

    def diagnostic(self, code: int, child_code: int | None) -> str:
        """Describe cleanup separately from the child's outcome without credentials."""
        status = "refused" if code == 1 else "unknown"
        text = (
            f"lock: release {status} for {self.task_id!r}; "
            f"child exit={child_code}; lease may remain held"
        )
        if code == 3 and self.intent is not None:
            arguments = [
                "synapse",
                "release",
                f"--name={self.name}",
                "--confirm-only",
                f"--idem-key={self.intent.operation_id}",
                f"--request-digest={self.intent.request_digest}",
                "--",
                self.task_id,
            ]
            if display_hub_uri(self.uri) != self.uri:
                text += "\nRestore the original hub URI privately in SYNAPSE_URI before recovery."
            else:
                arguments.insert(3, f"--uri={self.uri}")
            recovery = shlex.join(arguments)
            text += "\nDo not replay release. Read-only recovery: " + recovery
        return text

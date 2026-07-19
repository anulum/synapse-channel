# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one-shot authenticated guard-denial evidence reporter
"""Submit bounded, digest-only claim-guard denials to a durable hub."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType

MAX_GUARD_EVIDENCE_TIMEOUT = 30.0
"""Maximum duration of either reporter transport phase."""


class GuardEvidenceAgent(Protocol):
    """Minimal client surface needed by the one-shot evidence reporter."""

    running: bool

    async def connect(self) -> None:
        """Connect until cancelled."""

    async def wait_until_ready(self, *, timeout: float) -> bool:
        """Return whether hub registration completed before ``timeout``."""

    async def send_message(
        self,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        **extra: Any,
    ) -> None:
        """Send one evidence frame."""


GuardEvidenceAgentFactory = Callable[..., GuardEvidenceAgent]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reporter_name(identity: str, session_id: str, tool_use_id: str) -> str:
    """Reuse the guard's bounded state-query name for the evidence connection."""
    owner = _sha256_text(identity)[:12]
    call = f"{session_id}\0{tool_use_id}".encode()
    slot = int(hashlib.sha256(call).hexdigest()[:2], 16) % 16
    return f"claim-hook/{owner}-{slot:x}"


def _valid_timeout(timeout: float) -> float | None:
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0 or value > MAX_GUARD_EVIDENCE_TIMEOUT:
        return None
    return value


def guard_denial_digests(
    *,
    provider: str,
    identity: str,
    session_id: str,
    tool_use_id: str,
    paths: Sequence[str],
) -> tuple[str, str, str]:
    """Return actor, call, and scope digests without retaining their raw inputs."""
    actor_sha256 = _sha256_text(identity)
    call_sha256 = _canonical_digest(
        {
            "provider": provider,
            "session_id": session_id,
            "tool_use_id": tool_use_id,
        }
    )
    scope_sha256 = _canonical_digest({"paths": sorted(str(path) for path in paths)})
    return actor_sha256, call_sha256, scope_sha256


async def submit_guard_denial(
    *,
    provider: str,
    identity: str,
    session_id: str,
    tool_use_id: str,
    paths: Sequence[str],
    reason_code: str,
    uri: str,
    token: str | None,
    timeout: float,
    agent_factory: GuardEvidenceAgentFactory = SynapseAgent,
) -> bool:
    """Best-effort report one denial through a separately authenticated socket.

    The reporter intentionally refuses open-hub submission: without a token the
    hub has no credential principal it can attest as the record's provenance.
    Transport or validation failures return ``False`` and never weaken the
    original guard decision.
    """
    phase_timeout = _valid_timeout(timeout)
    if phase_timeout is None or not token:
        return False

    provider_name = str(provider).strip().lower()
    session = str(session_id)
    tool_call = str(tool_use_id)
    raw_paths = tuple(str(path) for path in paths)
    actor_sha256, call_sha256, scope_sha256 = guard_denial_digests(
        provider=provider_name,
        identity=str(identity),
        session_id=session,
        tool_use_id=tool_call,
        paths=raw_paths,
    )
    completed = asyncio.Event()
    accepted = False

    async def collect(data: dict[str, Any]) -> None:
        nonlocal accepted
        if data.get("type") == MessageType.GUARD_DENIAL_RECORDED:
            accepted = data.get("call_sha256") == call_sha256
            completed.set()
        elif data.get("type") in (MessageType.ERROR, MessageType.AUTH_DENIED):
            completed.set()

    agent: GuardEvidenceAgent | None = None
    connection: asyncio.Task[None] | None = None
    readiness: asyncio.Task[bool] | None = None
    response: asyncio.Task[bool] | None = None
    try:
        agent = agent_factory(
            _reporter_name(str(identity), session, tool_call),
            collect,
            uri=uri,
            verbose=False,
            token=token,
        )
        connection = asyncio.create_task(agent.connect())
        readiness = asyncio.create_task(agent.wait_until_ready(timeout=phase_timeout))
        done, _ = await asyncio.wait(
            {connection, readiness},
            timeout=phase_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if connection in done:
            return False
        if readiness not in done or not readiness.result():
            return False

        await agent.send_message(
            MessageType.GUARD_DENIAL,
            target="System",
            payload="",
            actor_sha256=actor_sha256,
            call_sha256=call_sha256,
            idem_key=f"guard-denial:{call_sha256}",
            path_count=len(raw_paths),
            provider=provider_name,
            reason_code=reason_code,
            scope_sha256=scope_sha256,
        )
        response = asyncio.create_task(completed.wait())
        done, _ = await asyncio.wait(
            {connection, response},
            timeout=phase_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        return response in done and accepted
    except (OSError, RuntimeError, ValueError):
        return False
    finally:
        if agent is not None:
            agent.running = False
        for task in (response, readiness, connection):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


__all__ = ["guard_denial_digests", "submit_guard_denial"]

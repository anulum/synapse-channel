# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded manual release and exact read-only recovery
"""Separate confirmed releases, explicit refusals and uncertain manual outcomes."""

from __future__ import annotations

import asyncio
import json
import math
import shlex
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from websockets.exceptions import ConnectionClosed

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import (
    closed_after_ready,
    describe_connect_failure,
    explain_silent_outcome,
)
from synapse_channel.core.protocol import SENDER_HUB, MessageType
from synapse_channel.core.release_confirmation import ReleaseIntent

AgentFactory = Callable[..., SynapseAgent]


def _load_release_receipt(path: str | Path) -> dict[str, Any]:
    """Load and validate a release receipt JSON object from ``path``."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("receipt must be a JSON object")
    return payload


def _receipt_list(
    payload: dict[str, Any],
    key: str,
    fallback: list[str] | None,
) -> list[str]:
    """Merge a repeated receipt field with explicit CLI values."""
    items: list[str] = []
    raw = payload.get(key)
    if raw is not None:
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError(f"receipt field '{key}' must be a list of strings")
        items.extend(raw)
    items.extend(fallback or [])
    return items


def _receipt_freshness(payload: dict[str, Any], fallback: float | None) -> float | None:
    """Validate finite freshness, preferring an explicit value over receipt input."""
    raw = fallback if fallback is not None else payload.get("freshness_seconds")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ValueError("receipt field 'freshness_seconds' must be a number")
    try:
        freshness = float(raw)
    except OverflowError as exc:
        raise ValueError("receipt field 'freshness_seconds' must be finite") from exc
    if not math.isfinite(freshness):
        raise ValueError("receipt field 'freshness_seconds' must be finite")
    return freshness


def _validate_release_receipt_identity(
    payload: dict[str, Any],
    *,
    task_id: str,
    name: str,
) -> None:
    """Reject receipts whose task or owner would release the wrong claim."""
    receipt_task = payload.get("task_id")
    receipt_owner = payload.get("owner")
    if receipt_task is not None and receipt_task != task_id:
        raise ValueError(f"receipt task_id {receipt_task!r} does not match {task_id!r}")
    if receipt_owner is not None and receipt_owner != name:
        raise ValueError(f"receipt owner {receipt_owner!r} does not match {name!r}")


async def _release(
    *,
    uri: str,
    name: str,
    task_id: str,
    evidence: list[str] | None = None,
    artifacts: list[str] | None = None,
    known_failures: list[str] | None = None,
    changed_files: list[str] | None = None,
    generated_artifacts: list[str] | None = None,
    approvals: list[str] | None = None,
    confidence: str = "",
    freshness_seconds: float | None = None,
    receipt: str | Path | None = None,
    receipt_json: bool = False,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
    attempts: int = 40,
    poll_interval: float = 0.05,
    reply_timeout: float | None = None,
    idem_key: str | None = None,
    request_digest: str | None = None,
    confirm_only: bool = False,
) -> int:
    """Send at most one manual release and recover only through exact durable reads.

    Receipt arguments keep the established merge and validation semantics.
    ``reply_timeout`` bounds the complete send and response, not just polling;
    the CLI defaults to 30 seconds, with a finite maximum of 300. ``attempts``
    and ``poll_interval`` retain the prior internal helper deadline override.
    ``confirm_only`` requires the original ``idem_key`` and ``request_digest``
    printed on an uncertain result and never sends a release or receipt fields.
    A fresh release first requires a correlated exact-query response. A legacy,
    malformed or missing response refuses the operation before any mutation.

    Returns
    -------
    int
        0 for a matching grant or durable confirmation, 1 for local validation,
        pre-send admission failure or explicit hub refusal, 3 for uncertainty.
        An unknown outcome never authorizes replaying a release.
    """
    name, task_id = name.strip(), task_id.strip()
    timeout = reply_timeout if reply_timeout is not None else attempts * poll_interval
    try:
        for value in (ready_timeout, timeout):
            if not math.isfinite(value) or not 0 < value <= 300:
                raise ValueError("release timeouts must be finite and greater than 0, at most 300")
        if confirm_only:
            if (
                receipt is not None
                or evidence
                or artifacts
                or known_failures
                or changed_files
                or generated_artifacts
                or approvals
                or confidence
                or freshness_seconds is not None
            ):
                raise ValueError("--confirm-only does not accept release receipt fields")
            intent = ReleaseIntent(name, task_id, idem_key or "", request_digest or "")
            fields: dict[str, Any] = {}
        else:
            if request_digest is not None:
                raise ValueError("--request-digest requires --confirm-only")
            seed = _load_release_receipt(receipt) if receipt is not None else {}
            _validate_release_receipt_identity(seed, task_id=task_id, name=name)
            fields = {
                key: _receipt_list(seed, key, items)
                for key, items in (
                    ("evidence", evidence),
                    ("artifacts", artifacts),
                    ("known_failures", known_failures),
                    ("changed_files", changed_files),
                    ("generated_artifacts", generated_artifacts),
                    ("approvals", approvals),
                )
            }
            fields["confidence"] = confidence or str(seed.get("confidence") or "")
            fields["freshness_seconds"] = _receipt_freshness(seed, freshness_seconds)
            # Validate the key before connecting, then bind the real prepared epoch.
            ReleaseIntent(
                name, task_id, idem_key if idem_key is not None else uuid.uuid4().hex, "0" * 64
            )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        label = "recovery request" if confirm_only else "receipt"
        print(f"invalid release {label} for '{task_id}': {exc}")
        return 1

    pending: tuple[Callable[[dict[str, Any]], bool], asyncio.Future[dict[str, Any]]] | None = None

    async def collect(data: dict[str, Any]) -> None:
        """Resolve only the currently correlated release or read-only query."""
        if pending is not None and not pending[1].done() and pending[0](data):
            pending[1].set_result(data)

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())

    async def await_reply(
        match: Callable[[dict[str, Any]], bool], send: Callable[[], Awaitable[None]]
    ) -> dict[str, Any] | None:
        """Register before sending, bound the whole exchange and always unregister."""
        nonlocal pending
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        pending = (match, future)

        async def exchange() -> dict[str, Any]:
            """Send once and await only the matching inbound result."""
            await send()
            return await future

        try:
            return await asyncio.wait_for(exchange(), timeout)
        except (TimeoutError, ConnectionClosed, OSError):
            return None
        finally:
            pending = None

    def from_hub(data: dict[str, Any]) -> bool:
        """Check hub origin, including a conflict referencing a pre-restart response."""
        if data.get("sender") != SENDER_HUB:
            return False
        if (
            data.get("type") == MessageType.ERROR
            and data.get("error_code") == "idempotency_conflict"
            and data.get("target") == name
        ):
            return True
        return data.get("hub_id") == agent.hub_id

    async def confirmation(intent: ReleaseIntent) -> dict[str, Any] | None:
        """Read the prepared operation through a fresh private correlation."""
        request_id = uuid.uuid4().hex
        reply = await await_reply(
            lambda data: (
                from_hub(data)
                and data.get("type") == MessageType.STATE_SNAPSHOT
                and data.get("target") == name
                and data.get("request_id") == request_id
            ),
            lambda: agent.request_release_confirmation(
                task_id, intent.operation_id, intent.request_digest, request_id
            ),
        )
        projection = reply.get("release_confirmation") if reply is not None else None
        return projection if isinstance(projection, dict) else None

    try:
        if not await agent.wait_until_ready(timeout=ready_timeout) or await closed_after_ready(
            agent
        ):
            print(
                describe_connect_failure(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1
        if not confirm_only:
            request = agent.prepare_release(
                task_id, idem_key=idem_key or uuid.uuid4().hex, **fields
            )
            intent = ReleaseIntent.from_request(request)
            probe = await confirmation(intent)
            if probe is not None and probe.get("status") == "confirmed":
                historical = intent.matching_receipt(probe)
                if historical is not None:
                    print(
                        json.dumps(historical, sort_keys=True)
                        if receipt_json
                        else f"release confirmed for '{task_id}' "
                        "(historical operation; no mutation replay)"
                    )
                    return 0
            if (
                probe is None
                or probe.get("status") != "unknown"
                or any(probe.get(key) != value for key, value in intent.as_query().items())
            ):
                print(
                    f"release refused for '{task_id}': hub did not establish exact confirmation "
                    "support; no release sent"
                )
                return 1

            def release_verdict(data: dict[str, Any]) -> bool:
                """Match this keyed grant or an addressed explicit refusal."""
                if not from_hub(data):
                    return False
                if data.get("type") == MessageType.ERROR:
                    return data.get("target") == name and (
                        data.get("error_code") == "idempotency_conflict"
                        or (
                            data.get("task_id") == task_id
                            and data.get("release_operation_id") == intent.operation_id
                        )
                    )
                if data.get("task_id") != task_id:
                    return False
                if data.get("type") == MessageType.RELEASE_DENIED:
                    return data.get("target") == name
                return (
                    data.get("type") == MessageType.RELEASE_GRANTED
                    and intent.matching_receipt(data) is not None
                )

            async def send_release() -> None:
                """Send exactly the prepared semantic request, including the retained epoch."""
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

            reply = await await_reply(release_verdict, send_release)
            if reply is not None:
                if reply.get("type") == MessageType.RELEASE_GRANTED:
                    receipt_output = intent.matching_receipt(reply)
                    print(
                        json.dumps(receipt_output, sort_keys=True)
                        if receipt_json
                        else f"released '{task_id}'"
                    )
                    return 0
                print(
                    f"release refused for '{task_id}': {reply.get('payload') or 'release denied'}"
                )
                return 1
        projection = await confirmation(intent)
        receipt_output = (
            intent.matching_receipt(projection)
            if isinstance(projection, dict) and projection.get("status") == "confirmed"
            else None
        )
        if receipt_output is not None:
            print(
                json.dumps(receipt_output, sort_keys=True)
                if receipt_json
                else f"release confirmed for '{task_id}' (historical operation; no mutation replay)"
            )
            return 0
        recovery = shlex.join(
            [
                "synapse",
                "release",
                f"--name={name}",
                f"--uri={uri}",
                "--confirm-only",
                f"--idem-key={intent.operation_id}",
                f"--request-digest={intent.request_digest}",
                "--",
                task_id,
            ]
        )
        print(
            f"release outcome unknown for '{task_id}': no confirmed result; do not replay release"
        )
        print(f"Read-only recovery: {recovery}")
        if agent.last_close_code is not None:
            print(
                explain_silent_outcome(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                    fallback="connection closed before confirmation",
                )
            )
        return 3
    finally:
        agent.running = False
        conn_task.cancel()
        await asyncio.gather(conn_task, return_exceptions=True)

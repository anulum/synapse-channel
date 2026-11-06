# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the official a2a-sdk client half of the interop journey
"""Drive the official ``a2a-sdk`` client against a running Synapse A2A bridge.

This script runs in the client's own environment, which holds ``a2a-sdk`` and
nothing from Synapse, so a client dependency cannot shadow a hub dependency.
It imports only the SDK, ``httpx`` and the standard library, and prints one JSON
document: the content-minimised steps it took and the first failed step.

Journey: resolve the Agent Card, then use the transport the SDK selects from
it to send a message, get the task, list tasks and cancel the task. Then send
one raw HTTP request with a browser ``Origin`` header, made outside the SDK
because the SDK sends no Origin; the bridge must refuse it.

Usage::

    python tools/interop_a2a_client.py URL BEARER_FILE MARKER
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_CODE_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")


def _code(value: object) -> str:
    text = str(value)[:64]
    return text if text and set(text) <= _CODE_CHARACTERS else "error"


class _Steps:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.task_id = ""

    def ok(self, name: str, route: str, request: list[str], response: list[str]) -> None:
        self.records.append(
            {
                "name": name,
                "route": route,
                "outcome": "ok",
                "request_keys": sorted(request),
                "response_keys": sorted(response),
            }
        )

    def fail(self, name: str, route: str, outcome: str) -> str:
        self.records.append(
            {
                "name": name,
                "route": route,
                "outcome": _code(outcome),
                "request_keys": [],
                "response_keys": [],
            }
        )
        return name


def _fields(message: Any) -> list[str]:
    return [field.name for field, _value in message.ListFields()]


async def _send(client: Any, marker: str, steps: _Steps) -> str:
    """Send the marker message; return the task id, or an empty string on failure."""
    from a2a.types import a2a_pb2

    try:
        request = a2a_pb2.SendMessageRequest()
        request.message.message_id = "interop-message-1"
        request.message.role = a2a_pb2.ROLE_USER
        request.message.parts.add().text = marker
        task_id = ""
        async for event in client.send_message(request):
            if event.HasField("task"):
                task_id = event.task.id
            last = event
    except Exception as exc:  # noqa: BLE001 - any client failure fails the step
        steps.fail("message-sent", "message:send", type(exc).__name__)
        return ""
    if not task_id:
        steps.fail("message-sent", "message:send", "no-task")
        return ""
    steps.ok("message-sent", "message:send", _fields(request), _fields(last))
    return str(task_id)


async def _task_steps(client: Any, task_id: str, steps: _Steps) -> str:
    """Get, list and cancel the task; return the first failed step, or an empty string."""
    from a2a.types import a2a_pb2

    name, route = "task-got", "tasks/get"
    try:
        got = await client.get_task(a2a_pb2.GetTaskRequest(id=task_id))
        if got.id != task_id:
            return steps.fail(name, route, "wrong-task")
        steps.ok(name, route, ["id"], _fields(got))
        name, route = "tasks-listed", "tasks/list"
        listed = await client.list_tasks(a2a_pb2.ListTasksRequest())
        if task_id not in {task.id for task in listed.tasks}:
            return steps.fail(name, route, "task-missing")
        steps.ok(name, route, [], _fields(listed))
        name, route = "task-cancelled", "tasks/cancel"
        cancelled = await client.cancel_task(a2a_pb2.CancelTaskRequest(id=task_id))
        if cancelled.status.state != a2a_pb2.TASK_STATE_CANCELED:
            return steps.fail(name, route, "not-cancelled")
        steps.ok(name, route, ["id"], _fields(cancelled))
    except Exception as exc:  # noqa: BLE001 - any client failure fails the step
        return steps.fail(name, route, type(exc).__name__)
    return ""


def _refusal_reason(body: object) -> str:
    """Return the AIP-193 reason of an error body, or an empty string."""
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    for detail in details if isinstance(details, list) else []:
        if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
            return str(detail["reason"])
    return ""


async def _cross_origin(url: str, headers: dict[str, str], steps: _Steps) -> str:
    """Show that a browser Origin alone makes the bridge refuse a request.

    The same raw request, made outside the SDK, must succeed without an
    ``Origin`` header and be refused with 403 and the reason
    ``ORIGIN_OR_HOST_NOT_ALLOWED`` with one. The reason is checked, never
    copied into the step.
    """
    import httpx

    name, route = "cross-origin-refused", "raw GET agent card with and without Origin"
    card = url.rstrip("/") + "/.well-known/agent-card.json"
    async with httpx.AsyncClient(headers=headers, timeout=15, trust_env=False) as raw:
        plain = await raw.get(card)
        refused = await raw.get(card, headers={"Origin": "https://untrusted.interop.test"})
    if plain.status_code != 200:
        return steps.fail(name, route, f"without-origin-{plain.status_code}")
    if refused.status_code != 403:
        return steps.fail(name, route, f"with-origin-{refused.status_code}")
    try:
        reason = _refusal_reason(refused.json())
    except ValueError:
        reason = ""
    if reason != "ORIGIN_OR_HOST_NOT_ALLOWED":
        return steps.fail(name, route, "wrong-refusal-reason")
    steps.ok(name, route, [], [])
    return ""


async def journey(url: str, bearer: str, marker: str, steps: _Steps) -> str:
    """Run the journey and return the first failed step's name, or an empty string."""
    import httpx
    from a2a.client import ClientConfig, ClientFactory
    from a2a.utils.constants import TransportProtocol

    headers = {"Authorization": "Bearer " + bearer}
    async with httpx.AsyncClient(headers=headers, timeout=15, trust_env=False) as http:
        try:
            # The bridge serves HTTP+JSON; the SDK's default would accept only JSON-RPC.
            factory = ClientFactory(
                ClientConfig(
                    streaming=False,
                    httpx_client=http,
                    supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
                )
            )
            client = await factory.create_from_url(url)
        except Exception as exc:  # noqa: BLE001 - any client failure fails the step
            return steps.fail("card-resolved", "GET agent card", type(exc).__name__)
        steps.ok("card-resolved", "GET agent card", [], [])
        task_id = await _send(client, marker, steps)
        if not task_id:
            return "message-sent"
        failed = await _task_steps(client, task_id, steps)
        if failed:
            return failed
        steps.task_id = task_id
        await client.close()
    return await _cross_origin(url, headers, steps)


def main(argv: list[str]) -> int:
    """Run the journey and print its steps as JSON; exit 0 even when a step fails."""
    url, bearer_file, marker = argv
    bearer = Path(bearer_file).read_text(encoding="utf-8").strip()
    steps = _Steps()
    failed = asyncio.run(journey(url, bearer, marker, steps))
    from importlib import metadata

    print(
        json.dumps(
            {
                "client_version": metadata.version("a2a-sdk"),
                "failed_step": failed,
                "steps": steps.records,
                "task_id": steps.task_id,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

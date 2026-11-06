# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the official MCP Python SDK against real Synapse processes
"""Drive the official MCP Python SDK through the fixed interop journey.

The journey, on stdio and on Streamable HTTP alike:

1. initialize, list the tools and the resources;
2. the first identity claims the task;
3. the second identity claims it too and is refused;
4. the first identity sends a directed message to the second;
5. it reads the board resource;
6. it releases the task.

The hub's journal must then hold the grant, the live-lease denial of the
second identity with no release in between, the chat and the release.

On stdio the two identities are names asserted under one hub token, so that
row is protocol interop; on HTTP they are signed subjects mapped to their
seats, which is the authenticated case.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, cast

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, RequestParamsMeta

from tools.interop_hub import (
    HTTP_SUBJECTS,
    PROJECT,
    RESOURCE,
    STDIO_NAMES,
    Provisioned,
    clean_environment,
    free_port,
    http_mcp_command,
    hub_command,
    owner_only_text,
    provision,
    running,
    stdio_mcp_command,
)
from tools.interop_journey import StepFailure, Steps, journey_record, judge, version_failure
from tools.interop_oracle import JournalOracle
from tools.interop_pins import pinned_hashes, pins
from tools.interop_record import JourneyRecord, StepRecord

CLIENT = "mcp-python-sdk"
JOURNEY = "claim-conflict-message-release"
MARKER = "synapse-interop-marker"
FIRST_JOURNAL_STEP = "journal-claim-granted"


@dataclass(frozen=True)
class Identities:
    """The two identities of one transport and the task they contend for.

    ``operation_ids`` marks the HTTP profile, where every mutation carries a
    stable ``synapse/operation-id`` and a task is declared before its claim.
    """

    first: str
    second: str
    task_id: str
    operation_ids: bool


STDIO = Identities(STDIO_NAMES[0], STDIO_NAMES[1], "INTEROP-STDIO-1", operation_ids=False)
HTTP = Identities(
    f"{PROJECT}/{HTTP_SUBJECTS[0]}",
    f"{PROJECT}/{HTTP_SUBJECTS[1]}",
    "INTEROP/http-1",
    operation_ids=True,
)


def _result_text(result: CallToolResult) -> str:
    structured = result.structured_content
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return str(structured["result"])
    return ""


async def _call(
    steps: Steps,
    session: ClientSession,
    name: str,
    tool: str,
    arguments: dict[str, object],
    expect: Callable[[bool, str], bool],
    *,
    who: Identities,
) -> None:
    route = f"tools/call {tool}"
    meta: dict[str, Any] | None = None
    if who.operation_ids:
        meta = {"synapse/operation-id": f"interop-{name}"}
    try:
        result = await session.call_tool(
            tool, arguments, meta=cast("RequestParamsMeta | None", meta)
        )
    except Exception as exc:  # noqa: BLE001 - any client failure fails the step
        raise steps.failed(name, route, type(exc).__name__) from None
    if not expect(result.is_error, _result_text(result)):
        raise steps.failed(name, route, "unexpected-result")
    steps.passed(name, route, request=arguments, response=result.structured_content)


REMOTE_REFUSAL = "remote MCP operation refused or unavailable"
"""The HTTP profile's content-free answer to any refused mutation."""


def _refused(who: Identities, error: bool, text: str) -> bool:
    """Return whether a conflicting claim was refused as the transport reports it.

    Stdio names the holder in a non-error result. The HTTP profile reports every
    refusal as one content-free error, so there the journal alone shows that it
    was the live-lease denial of the second identity.
    """
    if who.operation_ids:
        return error and text == REMOTE_REFUSAL
    return not error and text.startswith("claim denied") and who.first in text


async def _open(steps: Steps, session: ClientSession, name: str) -> None:
    try:
        await session.initialize()
        tools = await session.list_tools()
        resources = await session.list_resources()
    except Exception as exc:  # noqa: BLE001 - any client failure fails the step
        raise steps.failed(name, "initialize", type(exc).__name__) from None
    names = {tool.name for tool in tools.tools}
    if not {"synapse_claim", "synapse_release", "synapse_send"} <= names:
        raise steps.failed(name, "tools/list", "tools-missing")
    if "synapse://board" not in {str(resource.uri) for resource in resources.resources}:
        raise steps.failed(name, "resources/list", "board-missing")
    steps.passed(name, "initialize")


async def journey(
    steps: Steps,
    first: ClientSession,
    second: ClientSession,
    who: Identities,
    *,
    claim_arguments: dict[str, object],
) -> None:
    """Run the client half of the journey on two open sessions."""
    await _open(steps, first, "initialize-first")
    await _open(steps, second, "initialize-second")
    claim = {"task_id": who.task_id, **claim_arguments}
    if who.operation_ids:
        await _call(
            steps,
            first,
            "task-declared",
            "synapse_task_declare",
            {"task_id": who.task_id, "title": "interop journey"},
            lambda error, text: not error and text.startswith("declared"),
            who=who,
        )
    await _call(
        steps,
        first,
        "claim-granted",
        "synapse_claim",
        claim,
        lambda error, text: not error and text.startswith("claim granted"),
        who=who,
    )
    await _call(
        steps,
        second,
        "claim-conflict-refused",
        "synapse_claim",
        claim,
        lambda error, text: _refused(who, error, text),
        who=who,
    )
    await _call(
        steps,
        first,
        "message-sent",
        "synapse_send",
        {"target": who.second, "message": MARKER},
        lambda error, text: not error and text.startswith("sent to"),
        who=who,
    )
    try:
        board = await first.read_resource("synapse://board")
    except Exception as exc:  # noqa: BLE001 - any client failure fails the step
        raise steps.failed("board-read", "resources/read", type(exc).__name__) from None
    if not board.contents:
        raise steps.failed("board-read", "resources/read", "empty")
    steps.passed("board-read", "resources/read synapse://board")
    await _call(
        steps,
        first,
        "released",
        "synapse_release",
        {"task_id": who.task_id},
        lambda error, text: not error and text.startswith("released"),
        who=who,
    )


def journal_assertions(who: Identities) -> Callable[[JournalOracle], None]:
    """Return the journal half of the journey; the first assertion is the grant."""

    def check(oracle: JournalOracle) -> None:
        granted = oracle.claim_granted(who.task_id, who.first)
        denied = oracle.claim_denied(who.task_id, who.second, after=granted)
        oracle.no_release_between(who.task_id, granted, denied)
        oracle.chat_recorded(who.first, who.second, MARKER)
        oracle.released(who.task_id, after=denied)

    return check


@asynccontextmanager
async def _stdio_sessions(
    provisioned: Provisioned, hub_uri: str
) -> AsyncIterator[tuple[ClientSession, ClientSession]]:
    async with AsyncExitStack() as stack:
        sessions: list[ClientSession] = []
        for name in STDIO_NAMES:
            command = stdio_mcp_command(provisioned, hub_uri, name)
            parameters = StdioServerParameters(
                command=command[0],
                args=command[1:],
                cwd=str(provisioned.directory),
                env=clean_environment(),
            )
            read, write = await stack.enter_async_context(stdio_client(parameters))
            sessions.append(await stack.enter_async_context(ClientSession(read, write)))
        yield sessions[0], sessions[1]


@asynccontextmanager
async def _http_sessions(
    provisioned: Provisioned, url: str, *, audience_ok: bool
) -> AsyncIterator[tuple[ClientSession, ClientSession]]:
    context = ssl.create_default_context(cafile=str(provisioned.certificate))
    async with AsyncExitStack() as stack:
        sessions: list[ClientSession] = []
        for subject in HTTP_SUBJECTS:
            audience = RESOURCE if audience_ok else "https://other.interop.test/mcp"
            token = provisioned.access_token(subject, audience=audience)
            client = await stack.enter_async_context(
                httpx2.AsyncClient(
                    verify=context,
                    trust_env=False,
                    timeout=15,
                    headers={"Authorization": "Bearer " + token},
                )
            )
            read, write = await stack.enter_async_context(
                streamable_http_client(url + "/mcp", http_client=client)
            )
            sessions.append(await stack.enter_async_context(ClientSession(read, write)))
        yield sessions[0], sessions[1]


async def _guarded(
    steps: Steps, first: ClientSession, second: ClientSession, who: Identities
) -> str:
    """Run the client half and return the failed step's name, or an empty string.

    The failure is caught here, inside the sessions, so the SDK's task groups
    close normally instead of wrapping it in an exception group.
    """
    try:
        await journey(steps, first, second, who, claim_arguments={"task_only": True})
    except StepFailure as failure:
        return failure.step
    return ""


ClientHalf = Callable[[Provisioned, str, "str | None", str, Steps], tuple[str, str]]
"""A client half: ``(provisioned, hub_uri, http_url, control, steps)``.

It returns ``(failed_step, client_version)``, the version the client reports
it ran. ``http_url`` is the HTTPS MCP server's base URL, or ``None`` on stdio.
"""

CLIENT_SECONDS = 120.0
"""How long a client half may run before its row fails."""


async def _python_sessions(
    provisioned: Provisioned, hub_uri: str, http_url: str | None, control: str, steps: Steps
) -> str:
    try:
        if http_url is None:
            async with _stdio_sessions(provisioned, hub_uri) as (first, second):
                return await _guarded(steps, first, second, STDIO)
        async with _http_sessions(
            provisioned, http_url, audience_ok=control != "surface-disabled"
        ) as (first, second):
            return await _guarded(steps, first, second, HTTP)
    except Exception as exc:  # noqa: BLE001 - a session that cannot open fails its first step
        steps.records.append(StepRecord("session-open", "connect", type(exc).__name__))
        return "session-open"


async def _bounded(
    provisioned: Provisioned, hub_uri: str, http_url: str | None, control: str, steps: Steps
) -> str:
    try:
        return await asyncio.wait_for(
            _python_sessions(provisioned, hub_uri, http_url, control, steps), CLIENT_SECONDS
        )
    except TimeoutError:
        steps.records.append(StepRecord("client-run", "client sessions", "client-timeout"))
        return "client-run"


def python_client_half(
    provisioned: Provisioned, hub_uri: str, http_url: str | None, control: str, steps: Steps
) -> tuple[str, str]:
    """Run the official Python SDK's half of the journey, bounded in time."""
    failed = asyncio.run(_bounded(provisioned, hub_uri, http_url, control, steps))
    return failed, metadata.version("mcp")


def run_mcp_row(
    transport: str,
    control: str,
    directory: Path,
    *,
    client: str,
    integrity: str,
    client_half: ClientHalf,
) -> tuple[JourneyRecord, list[str]]:
    """Run one MCP client's journey on ``transport`` in ``control`` mode.

    The hub, and on HTTP the HTTPS MCP server, run here; ``client_half`` drives
    the client. A client that reports another version than its pin fails the
    row. Returns the record and the secrets the run used.
    """
    provisioned = provision(directory / "provision")
    db = directory / "hub.db"
    port = free_port()
    steps = Steps()
    hub = hub_command(provisioned, db, port, identity_binding=transport != "stdio")
    if control == "surface-disabled" and transport == "stdio":
        # The hub expects a different token, so it refuses both MCP servers.
        hub[hub.index("--token-file") + 1] = str(other_token(provisioned))
    hub_uri = f"ws://127.0.0.1:{port}"
    with running(hub, port=port, label="hub", log=directory / "hub.log"):
        if transport == "stdio":
            failed_step, version = client_half(provisioned, hub_uri, None, control, steps)
        else:
            http_port = free_port()
            with running(
                http_mcp_command(provisioned, hub_uri, http_port),
                port=http_port,
                label="HTTP MCP server",
                log=directory / "mcp-http.log",
            ):
                failed_step, version = client_half(
                    provisioned, hub_uri, f"https://127.0.0.1:{http_port}", control, steps
                )
    if not failed_step:
        failed_step = version_failure(steps, client, version, pins()[client])
    journal_sha256 = ""
    if not failed_step:
        failed_step, journal_sha256 = judge(
            steps,
            db,
            control=control,
            unrelated=directory / "unrelated.db",
            assertions=journal_assertions(STDIO if transport == "stdio" else HTTP),
        )
    record = journey_record(
        client=client,
        version=version,
        integrity=integrity,
        transport=transport,
        journey=JOURNEY,
        steps=steps,
        failed_step=failed_step,
        journal_sha256=journal_sha256,
    )
    return record, provisioned.secrets()


def run_journey(transport: str, control: str, directory: Path) -> tuple[JourneyRecord, list[str]]:
    """Run the Python SDK row on ``transport`` in ``control`` mode."""
    return run_mcp_row(
        transport,
        control,
        directory,
        client=CLIENT,
        integrity=pinned_hashes(CLIENT),
        client_half=python_client_half,
    )


def other_token(provisioned: Provisioned) -> Path:
    """Write a hub token file that the hub does not accept, for the stdio control."""
    return owner_only_text(
        provisioned.directory / "other.token", "not-the-hub-token-" + "0" * 32 + "\n"
    )

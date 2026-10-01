# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated durable hub inbox reads
"""Read exact-identity journal pages over the configured hub connection."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from websockets.exceptions import WebSocketException

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType, is_recipient
from synapse_channel.hub_inbox_cursor import (
    HubInboxCursor,
    hub_inbox_cursor_path,
    load_hub_inbox_cursor,
    save_hub_inbox_cursor,
)
from synapse_channel.machine_identity import machine_identity_agent_kwargs


def inbox_query(identity: str, cursor: HubInboxCursor, limit: int) -> dict[str, object]:
    """Build a versioned exact-identity request without changing the wire version.

    Parameters
    ----------
    identity : str
        Connected identity or its recognised receive-only sidecar's owner.
    cursor : HubInboxCursor
        Previously consumed hub-bound sequence.
    limit : int
        Maximum returned messages, from 1 to 100.

    Returns
    -------
    dict[str, object]
        Additive ``history_request`` fields; old hubs cannot confirm this schema.
    """
    return {
        "version": 1,
        "identity": identity,
        "hub_id": cursor.hub_id,
        "since_seq": cursor.seq,
        "limit": limit,
    }


def validate_inbox_page(
    response: dict[str, Any],
    identity: str,
    cursor: HubInboxCursor,
    *,
    roles: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate a journal page before any local cursor can advance.

    Parameters
    ----------
    response : dict[str, Any]
        Correlated response from the authenticated hub connection.
    identity : str
        Exact requested identity.
    cursor : HubInboxCursor
        Previously accepted hub binding and sequence.
    roles : Iterable[str], optional
        Full roles declared by this connection, used to validate role-directed rows.

    Returns
    -------
    dict[str, Any]
        Validated page, including explicit availability and pagination state.

    Raises
    ------
    ValueError
        When the hub is legacy, the page is malformed, or its cursor crosses hubs.
    """
    page = response.get("inbox_page")
    if not isinstance(page, dict) or type(page.get("version")) is not int or page["version"] != 1:
        raise ValueError("hub does not support durable inbox pages")
    if page.get("available") is not True:
        raise ValueError("hub refused the durable inbox query")
    hub_id, seq, messages = page.get("hub_id"), page.get("cursor"), page.get("messages")
    if (
        page.get("identity") != identity
        or not isinstance(hub_id, str)
        or not hub_id
        or (cursor.hub_id and cursor.hub_id != hub_id)
        or type(seq) is not int
        or seq < cursor.seq
        or seq > 9223372036854775807
        or type(page.get("has_more")) is not bool
        or not isinstance(messages, list)
        or len(messages) > 100
        or (page.get("has_more") and seq == cursor.seq)
    ):
        raise ValueError("invalid durable inbox page")
    previous = cursor.seq
    for frame in messages:
        if (
            not isinstance(frame, dict)
            or type(frame.get("seq")) is not int
            or not previous < frame["seq"] <= seq
            or frame.get("type") != MessageType.CHAT
            or frame.get("channel")
            or frame.get("sender") == identity
            or not is_recipient(str(frame.get("target") or "all"), identity, roles)
        ):
            raise ValueError("invalid durable inbox message")
        previous = frame["seq"]
    return page


async def read_hub_inbox(
    *,
    uri: str,
    identity: str,
    home: Path,
    token: str | None = None,
    limit: int = 50,
    timeout: float = 5.0,
) -> int:
    """Print one authenticated page and then persist its isolated local cursor.

    Parameters
    ----------
    uri, identity : str
        Hub endpoint and exact connected identity; no existing receiver is displaced.
    home : Path
        Owner-local coordination home.
    token : str or None, optional
        Hub secret read at runtime; never stored in a cursor.
    limit : int, optional
        Returned messages per page, from 1 to 100.
    timeout : float, optional
        Positive connection and response deadline in seconds.

    Returns
    -------
    int
        Zero after a validated page and durable cursor; one on refusal or failure.
        No failure falls back to an unrelated local feed.
    """
    agent: SynapseAgent | None = None
    task: asyncio.Task[None] | None = None
    try:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("invalid inbox read bounds")
        path = hub_inbox_cursor_path(home, uri, identity)
        cursor = load_hub_inbox_cursor(path)
        request_id = uuid.uuid4().hex
        reply: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

        async def receive(data: dict[str, Any]) -> None:
            if data.get("type") == MessageType.HISTORY_SNAPSHOT and not reply.done():
                if data.get("request_id") in (None, request_id):
                    reply.set_result(data)

        agent = SynapseAgent(
            identity,
            receive,
            uri=uri,
            token=token,
            verbose=False,
            **machine_identity_agent_kwargs(),
        )
        task = asyncio.create_task(agent.connect())
        if not await agent.wait_until_ready(timeout=timeout):
            raise ValueError("hub inbox connection is unavailable")
        await agent.send_message(
            MessageType.HISTORY_REQUEST,
            target="System",
            payload="durable inbox",
            request_id=request_id,
            inbox_query=inbox_query(identity, cursor, limit),
        )
        response = await asyncio.wait_for(reply, timeout=timeout)
        page = validate_inbox_page(response, identity, cursor, roles=agent.roles)
        if page["hub_id"] != agent.hub_id:
            raise ValueError("inbox page does not belong to the connected hub")
        print(
            json.dumps(
                {
                    **page,
                    "source": uri,
                    "transport_boundary": "read does not prove model processing",
                }
            ),
            flush=True,
        )
        save_hub_inbox_cursor(path, HubInboxCursor(page["hub_id"], page["cursor"]))
        return 0
    except (OSError, ValueError, asyncio.TimeoutError, WebSocketException):
        print(
            json.dumps(
                {
                    "available": False,
                    "identity": identity,
                    "error": "cannot read durable hub inbox; cursor unchanged",
                }
            ),
            flush=True,
        )
        return 1
    finally:
        if agent is not None:
            agent.running = False
            if agent.connection is not None:
                with contextlib.suppress(Exception):
                    await agent.connection.close()
        if task is not None:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

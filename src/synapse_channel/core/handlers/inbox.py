# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cursor-paged reads of an identity's durable inbox
"""Serve bounded inbox pages without granting access to another identity."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from synapse_channel.core.agent_liveness import waiter_sidecar_names
from synapse_channel.core.journal import EventKind
from synapse_channel.core.protocol import MessageType, is_recipient

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.handler_context import HandlerContext

    class InboxContext(HandlerContext, Protocol):
        """Capabilities consumed by inbox handlers and their callees."""

        def roles_of(self, name: str) -> tuple[str, ...]:
            """Return the roles ``name`` currently answers to (empty tuple if none)."""
            ...


INBOX_SCAN_LIMIT = 1000
"""Maximum journal rows inspected by one inbox request."""

INBOX_PAGE_BYTES = 7 * 1024 * 1024
"""Maximum encoded message bytes, below the client frame ceiling."""


async def handle_inbox_query(
    hub: InboxContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer an additive ``history_request.inbox_query`` with a durable page.

    Parameters
    ----------
    hub : InboxContext
        Authoritative hub whose journal is read.
    sender : str
        Connection identity already admitted by the hub's identity and ACL gates.
    data : dict[str, Any]
        Version 1 query, exact identity, hub-bound sequence cursor and page limit.
    websocket : object
        Requesting transport; the response is never broadcast.

    Notes
    -----
    Only self or a recognised receive-only sidecar can read this surface.
    Channel-tagged chat is excluded: private membership uses channel history.
    Cursor advancement records scanned journal rows, not model processing.
    """
    query = data.get("inbox_query")
    page: dict[str, Any] = {
        "version": 1,
        "available": False,
        "identity": sender,
        "hub_id": hub.hub_id,
        "cursor": 0,
        "messages": [],
        "has_more": False,
    }
    error = "invalid inbox query"
    if isinstance(query, dict):
        identity = query.get("identity")
        since = query.get("since_seq")
        limit = query.get("limit")
        bound_hub = query.get("hub_id")
        if (
            type(query.get("version")) is int
            and query["version"] == 1
            and isinstance(identity, str)
            and bool(identity.strip())
            and type(since) is int
            and 0 <= since <= 9223372036854775807
            and type(limit) is int
            and 1 <= limit <= 100
            and isinstance(bound_hub, str)
            and (bool(bound_hub) or since == 0)
        ):
            page["identity"] = identity
            page["cursor"] = since
            if sender != identity and sender not in waiter_sidecar_names(identity):
                error = "inbox identity is not owned by this connection"
            elif bound_hub and bound_hub != hub.hub_id:
                error = "inbox cursor belongs to a different hub"
            elif hub.journal is None:
                error = "durable inbox requires a journal"
            elif since > hub.journal.max_seq():
                error = "inbox cursor exceeds the retained journal"
            else:
                error = ""
                events = hub.journal.read_since(
                    since, kinds=(EventKind.CHAT,), limit=INBOX_SCAN_LIMIT + 1
                )
                messages: list[dict[str, Any]] = []
                scanned = 0
                message_bytes = 0
                for event in events[:INBOX_SCAN_LIMIT]:
                    frame = event.payload
                    if (
                        not frame.get("channel")
                        and frame.get("sender") != identity
                        and is_recipient(
                            str(frame.get("target") or "all"), identity, hub.roles_of(sender)
                        )
                    ):
                        message = {**frame, "seq": event.seq}
                        encoded_bytes = len(json.dumps(message).encode("utf-8"))
                        if message_bytes + encoded_bytes > INBOX_PAGE_BYTES:
                            if not messages:
                                error = "inbox message exceeds the response size limit"
                            break
                        messages.append(message)
                        message_bytes += encoded_bytes
                        page["cursor"] = event.seq
                        scanned += 1
                        if len(messages) == limit:
                            break
                    else:
                        page["cursor"] = event.seq
                        scanned += 1
                page.update(
                    available=not bool(error),
                    messages=messages,
                    has_more=scanned < len(events),
                )
    if error:
        page["error"] = error
    request_id = data.get("request_id")
    correlation = (
        {"request_id": request_id}
        if isinstance(request_id, str) and 0 < len(request_id) <= 128
        else {}
    )
    await hub.send_json(
        websocket,
        hub.system(
            "Durable inbox page",
            msg_type=MessageType.HISTORY_SNAPSHOT,
            target=sender,
            **correlation,
            inbox_page=page,
        ),
    )

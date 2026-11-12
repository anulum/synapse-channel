# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP inbox source selection and page queries
"""Select local or connected-hub inboxes behind the stable MCP bridge."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.owned_work import MAX_OWNED_CHARS, OwnedQuery
from synapse_channel.mcp.activity_page import ActivityPage, read_activity_page
from synapse_channel.mcp.hub_inbox import drain_hub_inbox
from synapse_channel.mcp.inbox import DEFAULT_MCP_INBOX_LIMIT, McpFeedInbox
from synapse_channel.mcp.inbox_page import read_inbox_page
from synapse_channel.mcp.owned_work import read_owned_work
from synapse_channel.mcp.pending_work import PendingSnapshot, read_pending_work
from synapse_channel.mcp.reply_exchange import McpReplyExchange


class _McpInboxQueries(McpReplyExchange):
    """Own source validation and cursor-safe reads for an attached hub client."""

    agent: SynapseAgent

    def __init__(
        self,
        *,
        name: str,
        uri: str,
        request_timeout: float,
        roles: Iterable[str],
        inbox_feed: str | Path | None,
        inbox_cursor: str | Path | None,
    ) -> None:
        """Configure the existing feed/hub contract before creating a client."""
        super().__init__(request_timeout)
        self.inbox_source = os.environ.get("SYN_INBOX_SOURCE", "feed")
        if self.inbox_source not in {"feed", "hub"}:
            raise ValueError("SYN_INBOX_SOURCE must be feed or hub")
        if self.inbox_source == "hub" and (inbox_feed is not None or inbox_cursor is not None):
            raise ValueError("local inbox paths cannot be combined with the hub source")
        self.inbox_uri = uri
        self.inbox_home = Path(os.environ.get("SYN_HOME", str(Path.home() / "synapse")))
        role_names = tuple(dict.fromkeys(role.strip() for role in roles if role.strip()))
        self.inbox_reader = McpFeedInbox(
            name,
            roles=role_names,
            feed_path=inbox_feed,
            cursor_path=inbox_cursor,
        )
        self.inbox_roles = role_names

    async def inbox(self, limit: int = DEFAULT_MCP_INBOX_LIMIT) -> str:
        """Read one bounded page from the selected feed or authenticated hub.

        ``SYN_INBOX_SOURCE=hub`` uses this bridge's connection and an independent
        endpoint/identity cursor. Availability is explicit; a read does not prove
        model processing. The default preserves offline local-feed behaviour.

        Parameters
        ----------
        limit : int, optional
            Maximum matching messages, bounded to 1–100.

        Returns
        -------
        str
            JSON page with source, availability, cursor and remaining-page state.
        """
        if self.inbox_source == "hub":
            return await drain_hub_inbox(
                self.agent,
                self._await_reply,
                uri=self.inbox_uri,
                home=self.inbox_home,
                limit=safe_int(
                    limit,
                    default=DEFAULT_MCP_INBOX_LIMIT,
                    min_value=1,
                    max_value=100,
                    allow_bool=False,
                ),
            )
        return self.inbox_reader.drain(limit)

    async def inbox_page(self, *, hub_id: str = "", after_seq: int = 0, limit: int = 50) -> str:
        """Read exact directed messages without consuming the local feed or cursor.

        Parameters
        ----------
        hub_id : str, optional
            Hub identifier from an accepted page; empty only for the initial read.
        after_seq : int, optional
            Last accepted scanned journal sequence, initially zero.
        limit : int, optional
            Native page size from one to one hundred, before exact-target filtering.

        Returns
        -------
        str
            Hub-bound page; the caller owns persistence and lost-reply retry.

        Raises
        ------
        ValueError
            When cursor bounds or the initial hub binding are invalid.
        RuntimeError
            When the native hub cannot confirm a valid page.
        """
        return await read_inbox_page(
            self.agent, self._await_reply, hub_id=hub_id, after_seq=after_seq, limit=limit
        )

    async def owned_work(
        self,
        *,
        project: str | None = None,
        mode: Literal["list", "read"] = "list",
        hub_id: str = "",
        after_key: str = "",
        revision: str = "",
        limit: int = 50,
        key: str = "",
        item_version: str = "",
        offset: int = 0,
        digest: str = "",
        limit_chars: int = MAX_OWNED_CHARS,
    ) -> str:
        """Bind full current-owned work reads to this bridge's existing native identity.

        The HTTP registration fixes project outside model arguments. Local stdio
        selects all this owner's work. A query owns no cursor and opens no socket.
        """
        query = OwnedQuery(
            identity=self.agent.name,
            hub_id=hub_id,
            project=project,
            mode=mode,
            after_key=after_key,
            revision=revision,
            limit=limit,
            key=key,
            item_version=item_version,
            offset=offset,
            digest=digest,
            limit_chars=limit_chars,
        )
        return await read_owned_work(self.agent, self._await_reply, query)

    async def pending_work(self, *, hub_id: str = "") -> PendingSnapshot:
        """Read complete historical recovery metadata on this existing connection."""
        return await read_pending_work(self.agent, self._await_reply, hub_id=hub_id)

    async def activity_page(
        self, *, hub_id: str = "", after_seq: int = 0, limit: int = 50, version: int = 1
    ) -> ActivityPage:
        """Read exact native wake metadata without committing any scan or inbox ACK.

        Parameters
        ----------
        hub_id : str
            Last accepted hub binding, empty only for an initial read.
        after_seq : int
            Observer-owned scanned journal position.
        limit : int
            Maximum metadata items from one through one hundred.
        version : int
            Version two includes content-free stream progress and termination.

        Returns
        -------
        ActivityPage
            Validated source families and cursor on this existing connection.
        """
        return await read_activity_page(
            self.agent,
            self._await_reply,
            hub_id=hub_id,
            after_seq=after_seq,
            limit=limit,
            version=version,
        )

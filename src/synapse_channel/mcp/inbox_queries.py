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

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.mcp.hub_inbox import drain_hub_inbox
from synapse_channel.mcp.inbox import DEFAULT_MCP_INBOX_LIMIT, McpFeedInbox
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

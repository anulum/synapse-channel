# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded local-feed inbox for the MCP adapter
"""Read a bridge identity's durable relay inbox in bounded cursor-safe pages."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote

from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.protocol import MessageType, is_recipient
from synapse_channel.mailbox_cursor import load_cursor, save_cursor
from synapse_channel.relay import decode_lite

DEFAULT_MCP_INBOX_LIMIT = 50
"""Default messages returned by one MCP inbox call."""

MAX_MCP_INBOX_LIMIT = 100
"""Hard output bound for one MCP inbox call."""


@dataclass(frozen=True)
class McpInboxPaths:
    """Durable relay feed and per-identity MCP cursor paths."""

    feed: Path
    cursor: Path


@dataclass(frozen=True)
class McpInboxPage:
    """One bounded inbox page and the cursor state after scanning it."""

    messages: tuple[dict[str, Any], ...]
    cursor: int
    has_more: bool
    available: bool
    error: str = ""


def default_inbox_paths(
    identity: str,
    *,
    env: Mapping[str, str] | None = None,
) -> McpInboxPaths:
    """Return the default local relay feed and safe flat cursor paths.

    Parameters
    ----------
    identity : str
        Exact bridge identity whose inbox is consumed.
    env : Mapping[str, str] or None, optional
        Environment supplying ``SYN_HOME``/``HOME``.

    Returns
    -------
    McpInboxPaths
        ``feed.ndjson`` plus an owner-local cursor under ``mcp-inbox-cursor``.
    """
    values = os.environ if env is None else env
    home = Path(values.get("HOME", str(Path.home()))).expanduser()
    syn_home = Path(values.get("SYN_HOME", str(home / "synapse"))).expanduser()
    return McpInboxPaths(
        feed=syn_home / "feed.ndjson",
        cursor=syn_home / "mcp-inbox-cursor" / quote(identity, safe=""),
    )


class McpFeedInbox:
    """Bounded, exact-identity reader over the local durable relay feed."""

    def __init__(
        self,
        identity: str,
        *,
        roles: Iterable[str] = (),
        feed_path: str | Path | None = None,
        cursor_path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        name = identity.strip()
        if not name:
            raise ValueError("MCP inbox identity must not be blank")
        defaults = default_inbox_paths(name, env=env)
        self.identity = name
        self.roles = tuple(dict.fromkeys(role.strip() for role in roles if role.strip()))
        self.feed_path = Path(feed_path).expanduser() if feed_path else defaults.feed
        self.cursor_path = Path(cursor_path).expanduser() if cursor_path else defaults.cursor

    def drain(self, limit: int = DEFAULT_MCP_INBOX_LIMIT) -> str:
        """Consume one bounded page and return a stable JSON tool result.

        Parameters
        ----------
        limit : int, optional
            Maximum returned messages, clamped to ``1..100``.

        Returns
        -------
        str
            JSON containing availability, identity, messages, cursor, and
            whether unread feed bytes remain.
        """
        bounded = safe_int(
            limit,
            default=DEFAULT_MCP_INBOX_LIMIT,
            min_value=1,
            max_value=MAX_MCP_INBOX_LIMIT,
            allow_bool=False,
        )
        page = self._read_page(bounded)
        payload = {
            "available": page.available,
            "cursor": page.cursor,
            "has_more": page.has_more,
            "identity": self.identity,
            "messages": list(page.messages),
            "source": str(self.feed_path),
            "transport_boundary": (
                "local durable relay bodies; reading does not prove model processing"
            ),
        }
        if page.error:
            payload["error"] = page.error
        return json.dumps(payload, indent=2, sort_keys=True)

    def _read_page(self, limit: int) -> McpInboxPage:
        """Scan complete relay lines until ``limit`` matching chats are collected."""
        original = load_cursor(self.cursor_path)
        try:
            with self.feed_path.open("rb") as handle:
                size = _file_size(handle)
                start = original if original <= size else 0
                handle.seek(start)
                messages, cursor = self._scan(handle, size=size, limit=limit)
        except FileNotFoundError:
            return McpInboxPage((), original, False, False, "local relay feed is missing")
        except OSError as exc:
            return McpInboxPage((), original, False, False, f"cannot read local relay feed: {exc}")

        if cursor != original:
            try:
                save_cursor(self.cursor_path, cursor)
            except OSError as exc:
                return McpInboxPage(
                    messages=tuple(messages),
                    cursor=original,
                    has_more=True,
                    available=False,
                    error=f"cannot persist MCP inbox cursor; messages may repeat: {exc}",
                )
        return McpInboxPage(
            messages=tuple(messages),
            cursor=cursor,
            has_more=cursor < size,
            available=True,
        )

    def _scan(self, handle: BinaryIO, *, size: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        """Return matching decoded rows and the last completely consumed byte."""
        messages: list[dict[str, Any]] = []
        cursor = handle.tell()
        while len(messages) < limit:
            line_start = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            line_end = handle.tell()
            text = raw.decode("utf-8", errors="replace").strip()
            try:
                row = json.loads(text) if text else None
            except json.JSONDecodeError:
                if line_end == size and not raw.endswith((b"\n", b"\r")):
                    handle.seek(line_start)
                    break
                cursor = line_end
                continue
            cursor = line_end
            if not isinstance(row, dict):
                continue
            message = decode_lite(row)
            if self._matches(message):
                messages.append(message)
        return messages, cursor

    def _matches(self, message: Mapping[str, Any]) -> bool:
        """Return whether a decoded relay row belongs in this bridge inbox."""
        if message.get("type") != MessageType.CHAT:
            return False
        if str(message.get("sender") or "") == self.identity:
            return False
        return is_recipient(
            str(message.get("target") or "all"),
            self.identity,
            roles=self.roles,
        )


def _file_size(handle: BinaryIO) -> int:
    """Return ``handle`` size while restoring its position."""
    position = handle.tell()
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(position)
    return size

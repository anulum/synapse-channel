# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded local-feed MCP inbox tests

from __future__ import annotations

import json
import stat
from pathlib import Path

from synapse_channel.mailbox_cursor import load_cursor, save_cursor
from synapse_channel.mcp.inbox import McpFeedInbox, default_inbox_paths
from synapse_channel.relay import append_jsonl, encode_lite


def _append(feed: Path, *, sender: str, target: str, payload: str, kind: str = "chat") -> None:
    append_jsonl(
        feed,
        encode_lite(
            {
                "type": kind,
                "sender": sender,
                "target": target,
                "payload": payload,
                "timestamp": 1.0,
                "msg_id": 1,
            }
        ),
    )


def test_default_paths_are_syn_home_scoped_and_identity_flat() -> None:
    paths = default_inbox_paths(
        "PROJ/client",
        env={"SYN_HOME": "/state/synapse", "HOME": "/home/me"},
    )

    assert paths.feed == Path("/state/synapse/feed.ndjson")
    assert paths.cursor == Path("/state/synapse/mcp-inbox-cursor/PROJ%2Fclient")


def test_inbox_pages_only_matching_messages_without_skipping_the_tail(tmp_path: Path) -> None:
    feed = tmp_path / "feed.ndjson"
    cursor = tmp_path / "cursor"
    _append(feed, sender="HUB", target="all", payload="presence", kind="presence")
    _append(feed, sender="PEER", target="OTHER", payload="not mine")
    _append(feed, sender="PEER", target="PROJ", payload="project")
    _append(feed, sender="PEER", target="PROJ/reviewer", payload="role")
    _append(feed, sender="PROJ/client", target="PROJ/client", payload="own")
    _append(feed, sender="PEER", target="all", payload="broadcast")
    inbox = McpFeedInbox(
        "PROJ/client",
        roles=("PROJ/reviewer",),
        feed_path=feed,
        cursor_path=cursor,
    )

    first = json.loads(inbox.drain(2))
    second = json.loads(inbox.drain(2))

    assert [message["payload"] for message in first["messages"]] == ["project", "role"]
    assert first["has_more"] is True
    assert [message["payload"] for message in second["messages"]] == ["broadcast"]
    assert second["has_more"] is False
    assert second["cursor"] == feed.stat().st_size
    assert load_cursor(cursor) == feed.stat().st_size
    assert stat.S_IMODE(cursor.stat().st_mode) == 0o600


def test_partial_tail_is_retried_after_it_becomes_complete(tmp_path: Path) -> None:
    feed = tmp_path / "feed.ndjson"
    cursor = tmp_path / "cursor"
    _append(feed, sender="PEER", target="PROJ/client", payload="complete")
    partial_start = feed.stat().st_size
    with feed.open("ab") as handle:
        handle.write(b'{"v":1')
    inbox = McpFeedInbox("PROJ/client", feed_path=feed, cursor_path=cursor)

    first = json.loads(inbox.drain())
    assert [message["payload"] for message in first["messages"]] == ["complete"]
    assert first["cursor"] == partial_start
    assert first["has_more"] is True

    with feed.open("ab") as handle:
        handle.write(
            b',"i":2,"ty":"chat","s":"PEER","to":"PROJ/client",'
            b'"p":"completed tail","t":1000,"h":"","c":""}\n'
        )
    second = json.loads(inbox.drain())
    assert [message["payload"] for message in second["messages"]] == ["completed tail"]
    assert second["has_more"] is False


def test_malformed_complete_rows_are_consumed_and_truncated_cursor_resets(tmp_path: Path) -> None:
    feed = tmp_path / "feed.ndjson"
    cursor = tmp_path / "cursor"
    feed.write_text("not-json\n[]\n", encoding="utf-8")
    _append(feed, sender="PEER", target="PROJ/client", payload="after malformed")
    save_cursor(cursor, 999_999)
    inbox = McpFeedInbox("PROJ/client", feed_path=feed, cursor_path=cursor)

    payload = json.loads(inbox.drain(True))

    assert [message["payload"] for message in payload["messages"]] == ["after malformed"]
    assert payload["cursor"] == feed.stat().st_size


def test_missing_or_unreadable_feed_is_explicitly_unavailable(tmp_path: Path) -> None:
    missing = McpFeedInbox(
        "PROJ/client",
        feed_path=tmp_path / "missing.ndjson",
        cursor_path=tmp_path / "missing.cursor",
    )
    unreadable = McpFeedInbox(
        "PROJ/client",
        feed_path=tmp_path,
        cursor_path=tmp_path / "dir.cursor",
    )

    missing_payload = json.loads(missing.drain())
    unreadable_payload = json.loads(unreadable.drain())

    assert missing_payload["available"] is False
    assert missing_payload["error"] == "local relay feed is missing"
    assert unreadable_payload["available"] is False
    assert "cannot read local relay feed" in unreadable_payload["error"]


def test_empty_feed_does_not_create_a_cursor(tmp_path: Path) -> None:
    feed = tmp_path / "feed.ndjson"
    cursor = tmp_path / "cursor"
    feed.touch()

    payload = json.loads(McpFeedInbox("PROJ/client", feed_path=feed, cursor_path=cursor).drain())

    assert payload["available"] is True
    assert payload["cursor"] == 0
    assert payload["messages"] == []
    assert cursor.exists() is False


def test_cursor_persistence_failure_is_explicit_and_replay_safe(tmp_path: Path) -> None:
    feed = tmp_path / "feed.ndjson"
    cursor_directory = tmp_path / "cursor"
    cursor_directory.mkdir()
    _append(feed, sender="PEER", target="PROJ/client", payload="repeatable")
    inbox = McpFeedInbox(
        "PROJ/client",
        feed_path=feed,
        cursor_path=cursor_directory,
    )

    payload = json.loads(inbox.drain())

    assert payload["available"] is False
    assert payload["cursor"] == 0
    assert payload["has_more"] is True
    assert [message["payload"] for message in payload["messages"]] == ["repeatable"]
    assert "messages may repeat" in payload["error"]


def test_blank_identity_is_refused(tmp_path: Path) -> None:
    try:
        McpFeedInbox("  ", feed_path=tmp_path / "feed", cursor_path=tmp_path / "cursor")
    except ValueError as exc:
        assert "must not be blank" in str(exc)
    else:
        raise AssertionError("blank MCP inbox identity was accepted")

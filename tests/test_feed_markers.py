# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the chat markers an operator names for the forward feed
"""A hub serves as ``chat_marker`` exactly the markers its operator names, and refuses bad ones."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from cli_e2e_helpers import run_cli
from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli_processes
from synapse_channel.core.feed import (
    FEED_CHAT_MARKER_MAX_CHARS,
    FEED_CHAT_MARKERS_MAX,
    FeedLedger,
    payload_sha256,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_defaults import DEFAULT_FEED_CHAT_MARKERS
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.hub_inbox import INBOX_READY_HANG_GUARD_SECONDS

_WAIT = INBOX_READY_HANG_GUARD_SECONDS
_HUB = "marker-hub"


def _chat(journal: EventStore, text: str, **frame: Any) -> dict[str, Any]:
    """Journal one chat frame as the hub keeps it and return its payload."""
    payload = {"type": "chat", "sender": "R/pub", "target": "all", "payload": text, **frame}
    journal.append(EventKind.CHAT, payload)
    return payload


async def _reply(handle: AgentHandle, msg_type: str, reply_type: str, **fields: Any) -> Any:
    request_id = uuid.uuid4().hex
    await handle.agent.send_message(msg_type, target="System", request_id=request_id, **fields)
    return await handle.recorder.wait_for(
        lambda m: m.get("type") == reply_type and m.get("request_id") == request_id,
        timeout=_WAIT,
    )


def _query(consumer: str) -> dict[str, Any]:
    return {
        "version": 1,
        "consumer": consumer,
        "hub_id": "",
        "journal_instance": "",
        "after_seq": 0,
        "kinds": ["chat_marker"],
        "limit": 100,
    }


async def test_a_hub_serves_and_binds_only_the_markers_its_operator_names(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    lesson = _chat(journal, "Lesson: keep the cursor")
    finding = _chat(journal, "Finding: a default marker")
    _chat(journal, "Lesson: to one agent", target="R/other")
    _chat(journal, "Lesson: in a channel", channel="ops")
    _chat(journal, "lesson: another case")
    config = HubConfig(journal=journal, hub_id=_HUB, feed_chat_markers=("Lesson:", "Rule:"))
    try:
        async with running_hub(SynapseHub.from_config(config)) as (hub, uri):
            one = await connect_agent("R/one", uri)
            two = await connect_agent("R/two", uri)
            try:
                page = (
                    await _reply(
                        one, "history_request", "history_snapshot", feed_query=_query("R/one")
                    )
                )["feed_page"]
                bound = (
                    await _reply(
                        one,
                        "feed_bind",
                        "feed_bind_result",
                        consumer="R/one",
                        hub_id="",
                        proof={"kind": "chat_marker", "payload_sha256": payload_sha256(lesson)},
                    )
                )["feed_bind"]
                unserved = (
                    await _reply(
                        two,
                        "feed_bind",
                        "feed_bind_result",
                        consumer="R/two",
                        hub_id="",
                        proof={"kind": "chat_marker", "payload_sha256": payload_sha256(finding)},
                    )
                )["feed_bind"]
            finally:
                await close_agents(one, two)
            markers = hub.feed_ledger.markers
    finally:
        journal.close()

    assert markers == ("Lesson:", "Rule:")
    assert [row["payload"]["payload"] for row in page["rows"]] == ["Lesson: keep the cursor"]
    assert (bound["bound"], bound["after_seq"]) == (True, page["rows"][0]["seq"])
    # A default marker is not served once the operator names others, so it proves nothing.
    assert (unserved["bound"], unserved["code"]) == (False, "legacy_binding_unverified")


def test_a_hub_without_named_markers_serves_the_defaults() -> None:
    assert SynapseHub.from_config(HubConfig()).feed_ledger.markers == DEFAULT_FEED_CHAT_MARKERS
    assert DEFAULT_FEED_CHAT_MARKERS == ("Finding:", "Decision:")


@pytest.mark.parametrize(
    ("markers", "reason"),
    [
        ((), "1 to 16 chat markers, not 0"),
        (tuple(f"M{index}:" for index in range(FEED_CHAT_MARKERS_MAX + 1)), "not 17"),
        (("",), "text without surrounding whitespace"),
        ((" Lesson:",), "text without surrounding whitespace"),
        (("Lesson: ",), "text without surrounding whitespace"),
        ((7,), "text without surrounding whitespace, not 7"),
        (("L" * (FEED_CHAT_MARKER_MAX_CHARS + 1),), "at most 64 characters, not 65"),
        (("Les\tson:",), "only printable characters"),
        (("Lesson:", "Rule:", "Lesson:"), "'Lesson:' is named twice"),
    ],
)
def test_markers_the_feed_cannot_serve_are_refused_before_a_hub_exists(
    markers: tuple[object, ...], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        SynapseHub.from_config(HubConfig(feed_chat_markers=cast(tuple[str, ...], markers)))
    with pytest.raises(ValueError, match=reason):
        FeedLedger(cast(tuple[str, ...], markers))


def test_the_bounds_themselves_are_accepted() -> None:
    widest = tuple(f"M{index:02d}:" + "x" * (FEED_CHAT_MARKER_MAX_CHARS - 4) for index in range(16))
    assert all(len(marker) == FEED_CHAT_MARKER_MAX_CHARS for marker in widest)
    assert FeedLedger(widest).markers == widest
    assert FeedLedger(("Ü:",)).markers == ("Ü:",)


def test_the_hub_command_passes_named_markers_and_keeps_the_defaults_otherwise() -> None:
    captured: list[tuple[str, ...]] = []

    def build_hub(**kwargs: Any) -> SynapseHub:
        hub = SynapseHub(**kwargs)
        captured.append(hub.feed_ledger.markers)
        return hub

    named = cli_processes._cmd_hub(
        _hub_ns(feed_chat_marker=["Lesson:", "Rule:"]), runner=_close_runner, hub_factory=build_hub
    )
    default = cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub)

    assert (named, default) == (0, 0)
    assert captured == [("Lesson:", "Rule:"), DEFAULT_FEED_CHAT_MARKERS]


def test_the_hub_command_refuses_a_bad_marker_before_it_opens_the_journal(tmp_path: Path) -> None:
    db = tmp_path / "journal" / "hub.db"
    db.parent.mkdir()

    result = run_cli(
        "hub", "--db", str(db), "--feed-chat-marker", "Lesson:", "--feed-chat-marker", " x"
    )

    assert result.returncode == 2, result.stderr
    assert "synapse hub: invalid --feed-chat-marker: a chat marker is text without" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(db.parent.iterdir()) == []

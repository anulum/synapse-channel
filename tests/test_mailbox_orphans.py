# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — orphaned mailboxes are found from the journal alone
"""A name with pending mail and no journalled activity for long enough is an orphan.

The core tests build journal events directly. The command tests write a real
event store and read it back read-only through ``synapse mailbox orphans``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import build_parser
from synapse_channel.cli_mailbox import DEFAULT_LIMIT, _cmd_orphans, _positive_days
from synapse_channel.core.event_row_mac import load_or_create_row_mac_key, row_mac_key_path
from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_orphans import (
    MailboxStanding,
    mailbox_standings,
    orphaned_mailboxes,
    project_of,
)
from synapse_channel.core.persistence import EventStore, StoredEvent

DAY = 86_400.0
NOW = 100 * DAY


def _chat(seq: int, ts: float, sender: str, target: str, **extra: Any) -> StoredEvent:
    payload = {"sender": sender, "target": target, "payload": f"m{seq}", **extra}
    return StoredEvent(seq, ts, EventKind.CHAT, payload)


def _mark(seq: int, ts: float, identity: str, through: int) -> StoredEvent:
    payload = {"identity": identity, "through_seq": through, "source": "ack"}
    return StoredEvent(seq, ts, EventKind.MAILBOX_WATERMARK, payload)


def _by_name(events: list[StoredEvent]) -> dict[str, MailboxStanding]:
    return {item.identity: item for item in mailbox_standings(events)}


def test_pending_mail_counts_only_directed_chats_after_the_watermark() -> None:
    events = [
        _chat(1, 1.0, "P/a", "P/b"),
        _chat(2, 2.0, "P/a", "P/b"),
        _mark(3, 3.0, "P/b", 1),
        _chat(4, 4.0, "P/a", "P/b"),
        _chat(5, 5.0, "P/b", "P/b"),
        _chat(6, 6.0, "P/a", "all"),
        _chat(7, 7.0, "P/a", "P/b", channel="ops"),
        _chat(8, 8.0, "P/a", "P/b, ,Q/c"),
    ]
    standing = _by_name(events)["P/b"]
    assert (standing.pending, standing.watermark) == (3, 1)
    assert standing.oldest_pending_ts == 2.0
    assert standing.last_activity_ts == 5.0
    assert _by_name(events)["Q/c"].pending == 1


def test_project_and_glob_targets_reach_every_listed_name() -> None:
    """A bare project and a single-name glob are directed; ``P/*`` is an audience, as in the hub."""
    events = [
        _chat(1, 1.0, "X/z", "P/a"),
        _chat(2, 2.0, "X/z", "P/b"),
        _chat(3, 3.0, "X/z", "P"),
        _chat(4, 4.0, "X/z", "P/?"),
        _chat(5, 5.0, "X/z", "seat@OTHER-HUB"),
        _chat(6, 6.0, "X/z", "P/*"),
    ]
    standings = _by_name(events)
    assert standings["P/a"].pending == 3
    assert standings["P/b"].pending == 3
    assert "seat@OTHER-HUB" not in standings
    assert project_of("P/a") == "P" and project_of("bare") == "bare"


def test_a_watermark_never_counts_beyond_its_own_event() -> None:
    events = [_chat(1, 1.0, "X/z", "P/a"), _mark(2, 2.0, "P/a", 99), _chat(3, 3.0, "X/z", "P/a")]
    standing = _by_name(events)["P/a"]
    assert (standing.watermark, standing.pending) == (2, 1)


def test_a_sidecar_counts_as_activity_of_its_owner() -> None:
    events = [_chat(1, 10.0, "X/z", "P/a"), _chat(2, 20.0, "P/a-rx", "X/z")]
    assert _by_name(events)["P/a"].last_activity_ts == 20.0


def test_events_without_a_name_change_nothing() -> None:
    events = [
        StoredEvent(1, 1.0, EventKind.CLAIM, {"task_id": "T"}),
        _mark(2, 2.0, " ", 1),
        _chat(3, 3.0, "", "P/a"),
    ]
    standings = _by_name(events)
    assert list(standings) == ["P/a"]
    assert standings["P/a"].last_activity_ts is None


def test_orphans_are_quiet_names_with_pending_mail() -> None:
    events = [
        _chat(1, NOW - 30 * DAY, "X/z", "P/old"),
        _chat(2, NOW - 30 * DAY, "P/old", "X/z"),
        _chat(3, NOW - 3 * DAY, "X/z", "P/recent"),
        _chat(4, NOW - 3 * DAY, "P/recent", "X/z"),
        _chat(5, NOW - 2 * DAY, "X/z", "P/never"),
        _chat(6, NOW - 2 * DAY, "X/z", "P/never"),
        _chat(7, NOW - 2 * DAY, "X/z", "P/connected"),
        _chat(8, NOW - 2 * DAY, "X/z", "Q/other"),
        _chat(9, NOW - 9 * DAY, "P/drained", "X/z"),
        _mark(10, NOW - 9 * DAY, "P/drained", 9),
        _chat(11, NOW - 7 * DAY, "P/edge", "X/z"),
        _chat(12, NOW - 7 * DAY, "X/z", "P/edge"),
    ]
    standings = mailbox_standings(events)
    orphans = orphaned_mailboxes(
        standings, now=NOW, dormant_after_days=7, active=["P/connected-rx"], project="P"
    )
    assert [item.identity for item in orphans] == ["P/never", "P/edge", "P/old"]
    assert orphans[0].dormant_for(NOW) is None
    assert orphans[2].dormant_for(NOW) == 30 * DAY
    everyone = orphaned_mailboxes(standings, now=NOW, dormant_after_days=7)
    assert {item.identity for item in everyone} == {
        "P/never",
        "P/edge",
        "P/old",
        "P/connected",
        "Q/other",
    }


@pytest.mark.parametrize("days", [0, -1.0, float("nan")])
def test_the_dormancy_period_must_be_positive(days: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        orphaned_mailboxes([], now=NOW, dormant_after_days=days)


def _store(tmp_path: Path) -> Path:
    db = tmp_path / "hub.db"
    with EventStore(db) as store:
        store.append(EventKind.CHAT, {"sender": "X/z", "target": "P/gone", "payload": "a"}, ts=1.0)
        store.append(EventKind.CHAT, {"sender": "P/gone", "target": "X/z", "payload": "b"}, ts=2.0)
        store.append(EventKind.CHAT, {"sender": "X/z", "target": "P/gone", "payload": "c"}, ts=3.0)
        store.append(EventKind.CHAT, {"sender": "X/z", "target": "P/here", "payload": "d"}, ts=4.0)
    return db


def _orphans(db: Path, *extra: str, now: float = NOW) -> tuple[int, list[str]]:
    args = build_parser().parse_args(["mailbox", "orphans", str(db), *extra])
    lines: list[str] = []
    return _cmd_orphans(args, clock=lambda: now, write=lines.append), lines


def test_the_command_lists_orphans_from_a_real_store(tmp_path: Path) -> None:
    db = _store(tmp_path)
    code, lines = _orphans(db)
    assert code == 0
    assert lines == [
        "      2  P/gone  last active 1970-01-01  oldest pending 1970-01-01",
        "      1  P/here  last active never  oldest pending 1970-01-01",
        "      1  X/z  last active 1970-01-01  oldest pending 1970-01-01",
        "4 pending messages in 3 orphaned mailboxes (quiet for 7 days or more)",
    ]
    code, lines = _orphans(db, "--active", "P/here", "--project", "P")
    assert code == 0 and len(lines) == 2


def test_the_command_reports_no_orphans_and_writes_json(tmp_path: Path) -> None:
    db = _store(tmp_path)
    code, lines = _orphans(db, "--dormant-after-days", "200", "--active", "P/here", now=3 * DAY)
    assert (code, lines) == (0, ["no orphaned mailboxes (quiet for 200 days or more)"])
    code, lines = _orphans(db, "--json")
    document = json.loads(lines[0])
    assert document["orphaned_names"] == 3 and document["orphaned_messages"] == 4
    assert document["orphans"][0] == {
        "identity": "P/gone",
        "project": "P",
        "pending": 2,
        "watermark": 0,
        "last_activity": "1970-01-01",
        "quiet_days": round((NOW - 2.0) / DAY, 1),
        "oldest_pending": "1970-01-01",
    }
    assert document["orphans"][1]["identity"] == "P/here"
    assert document["orphans"][1]["quiet_days"] is None
    assert document["roles"] == "not recorded in the journal"


def test_the_text_view_is_bounded_unless_all_is_asked(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    with EventStore(db) as store:
        for index in range(DEFAULT_LIMIT + 2):
            payload = {"sender": "X/z", "target": f"P/s{index:02d}", "payload": "x"}
            store.append(EventKind.CHAT, payload, ts=1.0)
    _code, lines = _orphans(db)
    assert len(lines) == DEFAULT_LIMIT + 1
    assert lines[-1].endswith("; 2 more with --all")
    _code, lines = _orphans(db, "--all")
    assert len(lines) == DEFAULT_LIMIT + 3


def test_the_row_key_beside_the_store_is_used_by_default(tmp_path: Path) -> None:
    db = _store(tmp_path)
    load_or_create_row_mac_key(row_mac_key_path(db), current_max_seq=4, log_has_macs=False)
    assert _orphans(db)[0] == 0
    explicit = tmp_path / "elsewhere.key"
    load_or_create_row_mac_key(explicit, current_max_seq=4, log_has_macs=False)
    assert _orphans(db, "--row-mac-key", str(explicit))[0] == 0


def test_a_missing_store_or_key_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _orphans(tmp_path / "absent.db")[0] == 1
    assert "synapse mailbox orphans: existing event journal is required" in capsys.readouterr().err
    db = _store(tmp_path)
    assert _orphans(db, "--row-mac-key", str(tmp_path / "no.key"))[0] == 1
    assert "row-authentication key" in capsys.readouterr().err


@pytest.mark.parametrize("text", ["0", "-2", "inf", "seven"])
def test_the_day_option_refuses_what_is_not_a_positive_number(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_days(text)
    assert _positive_days("1.5") == 1.5

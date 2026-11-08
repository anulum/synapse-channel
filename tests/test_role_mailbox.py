# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — role mailbox keys, the role/personal split, and the tracker's role mailboxes
"""Mail addressed to a role waits once, in the role's own mailbox, until a holder acks it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_pending import MailboxPendingTracker
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import is_recipient
from synapse_channel.core.role_mailbox import (
    ROLE_MAILBOX_PREFIX,
    addresses_role,
    is_role_mailbox_key,
    mailbox_project,
    mailbox_role,
    pending_in_mailbox,
    role_mailbox_key,
)

ROLE = "P/reviewer"
KEY = "role:P/reviewer"


def _chat(store: EventStore, target: str, *, sender: str = "X/peer", channel: str = "") -> int:
    payload: dict[str, Any] = {"sender": sender, "target": target, "type": "chat", "payload": "hi"}
    if channel:
        payload["channel"] = channel
    return store.append(EventKind.CHAT, payload)


def _payload(target: str, *, sender: str = "X/peer", channel: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"sender": sender, "target": target}
    if channel:
        payload["channel"] = channel
    return payload


def test_role_keys_are_prefixed_reserved_and_parsed_back() -> None:
    assert ROLE_MAILBOX_PREFIX == "role:"
    assert role_mailbox_key(" P/reviewer ") == KEY
    assert is_role_mailbox_key(KEY) and is_role_mailbox_key(" ROLE:P/x")
    assert not is_role_mailbox_key("P/reviewer")
    assert mailbox_role(KEY) == ROLE
    assert mailbox_role(" Role: P/x ") == "P/x"
    assert mailbox_role("role:") is None
    assert mailbox_role("P/reviewer") is None


def test_a_mailbox_belongs_to_its_name_or_its_role_project() -> None:
    assert mailbox_project("P/seat") == "P"
    assert mailbox_project("solo") == "solo"
    assert mailbox_project(KEY) == "P"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("P/reviewer", True),
        ("X/a, P/reviewer", True),
        ("P/review?r", True),
        ("P/[r]eviewer", True),
        ("P", False),
        ("P/*", False),
        ("all", False),
        ("", False),
        ("P/other", False),
        (" , ", False),
    ],
)
def test_the_role_clause_matches_a_role_like_is_recipient(target: str, expected: bool) -> None:
    assert addresses_role(target, ROLE) is expected


def test_an_empty_role_is_never_addressed() -> None:
    assert addresses_role("P/reviewer", "") is False


@pytest.mark.parametrize(
    "target",
    ["P/bob", "P", "P/reviewer", "P/bob,P/reviewer", "P/b?b", "X/other", "all", "P/*"],
)
def test_the_personal_and_role_clauses_together_equal_is_recipient(target: str) -> None:
    payload = _payload(target)
    either = pending_in_mailbox(payload, "P/bob") or pending_in_mailbox(payload, KEY)
    directed = target not in ("all",) and "*" not in target
    assert either is (directed and is_recipient(target, "P/bob", (ROLE,)))


def test_pending_in_mailbox_skips_channels_and_the_names_own_mail() -> None:
    assert pending_in_mailbox(_payload("P/bob"), "P/bob") is True
    assert pending_in_mailbox(_payload("P/bob", sender="P/bob"), "P/bob") is False
    assert pending_in_mailbox(_payload("P/bob", channel="c"), "P/bob") is False
    assert pending_in_mailbox(_payload(ROLE, channel="c"), KEY) is False
    assert pending_in_mailbox(_payload(ROLE, sender="P/bob"), KEY) is True
    assert pending_in_mailbox(_payload(ROLE), "P/bob") is False
    assert pending_in_mailbox({"sender": "X/peer"}, "P/bob") is False


def test_a_chat_to_the_role_and_the_name_waits_in_both(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        _chat(store, ROLE)
        _chat(store, f"P/bob,{ROLE}")
        _chat(store, "P/bob")
        tracker = MailboxPendingTracker(store)
        counts = tracker.snapshot(["P/bob"], lambda _name: [ROLE])
    assert counts == {"P/bob": 2, KEY: 2}


def test_a_live_chat_to_a_known_role_counts_at_the_role(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        tracker = MailboxPendingTracker(store)
        assert tracker.snapshot(["P/bob"], lambda _name: [ROLE]) == {"P/bob": 0, KEY: 0}
        seq = _chat(store, ROLE)
        tracker.observe_chat(seq, {"sender": "X/peer", "target": ROLE})
        counts = tracker.snapshot(["P/bob"], lambda _name: [ROLE])
    assert counts == {"P/bob": 0, KEY: 1}
    assert tracker.has_role_mailbox(ROLE)
    assert not tracker.has_role_mailbox("P/other")


def test_a_glob_part_reaches_a_role_mailbox_by_its_role(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        _chat(store, "P/review?r")
        _chat(store, "P/bo?")
        tracker = MailboxPendingTracker(store)
        counts = tracker.snapshot(["P/bob"], lambda _name: [ROLE])
    assert counts == {"P/bob": 1, KEY: 1}


def test_ack_by_a_holder_advances_the_role_and_a_cursor_does_not(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        first = _chat(store, ROLE)
        second = _chat(store, ROLE)
        tracker = MailboxPendingTracker(store)
        assert tracker.advance("P/bob", second, roles=[ROLE], source="cursor") is True
        assert tracker.watermark_for(KEY) == 0
        assert tracker.pending_count(KEY) == 2
        assert tracker.acknowledge("P/bob", first, roles=[ROLE]) is True
        assert tracker.pending_count(KEY) == 1
        marks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
    assert [mark.payload for mark in marks] == [
        {"identity": "P/bob", "through_seq": second, "source": "cursor"},
        {"identity": KEY, "through_seq": first, "source": "ack"},
    ]


def test_a_chat_to_two_held_roles_advances_both_on_one_ack(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        seq = _chat(store, f"{ROLE},P/triage")
        tracker = MailboxPendingTracker(store)
        assert tracker.acknowledge("P/bob", seq, roles=["P/idle", ROLE, "P/triage"]) is True
    assert tracker.watermark_for(KEY) == seq
    assert tracker.watermark_for("role:P/triage") == seq
    assert tracker.watermark_for("role:P/idle") == 0
    assert tracker.watermark_for("P/bob") == 0


def test_ack_of_a_missing_chat_or_without_a_store_is_refused(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        tracker = MailboxPendingTracker(store)
        assert tracker.acknowledge("P/bob", 7, roles=[ROLE]) is False
    without = MailboxPendingTracker(None)
    assert without.learn_roles([" P/x ", "", "P/x"]) == ("P/x",)
    assert without.known_identities == ()
    assert without.pending_count(KEY) == 0
    assert without.role_backlog([ROLE], limit=5) == ()


def test_a_blank_mailbox_has_no_pending_count(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        assert MailboxPendingTracker(store).pending_count("  ") == 0


def test_role_backlog_finds_role_mail_however_far_back(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        early = _chat(store, ROLE)
        for _ in range(5):
            _chat(store, "P/bob")
        both = _chat(store, f"{ROLE},P/triage")
        _chat(store, "X/elsewhere")
        tracker = MailboxPendingTracker(store)
        backlog = tracker.role_backlog([ROLE, "P/triage", "P/idle"], limit=10)
        limited = tracker.role_backlog([ROLE], limit=1)
    assert [event.seq for event in backlog] == [early, both]
    assert [event.seq for event in limited] == [early]


def test_role_backlog_skips_a_role_with_nothing_pending(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        _chat(store, "P/bob")
        tracker = MailboxPendingTracker(store)
        assert tracker.role_backlog([ROLE], limit=10) == ()


def test_a_known_role_replaces_a_personal_entry_of_the_same_string(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        seq = _chat(store, ROLE)
        tracker = MailboxPendingTracker(store)
        assert ROLE in tracker.known_identities
        tracker.learn_roles([ROLE])
        assert ROLE not in tracker.known_identities
        assert tracker.advance(ROLE, seq, source="cursor") is False
        _chat(store, ROLE)
        tracker.observe_chat(seq + 1, {"sender": "X/peer", "target": ROLE})
    assert ROLE not in tracker.known_identities
    assert KEY in tracker.known_identities


def test_restart_restores_role_watermarks_and_drops_the_plain_role_name(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    with EventStore(path) as store:
        first = _chat(store, ROLE)
        _chat(store, ROLE)
        tracker = MailboxPendingTracker(store)
        assert tracker.acknowledge("P/bob", first, roles=[ROLE]) is True
        store.append(
            EventKind.MAILBOX_WATERMARK,
            {"identity": ROLE, "through_seq": first, "source": "cursor"},
        )
    with EventStore(path) as reopened:
        restored = MailboxPendingTracker(reopened)
        counts = restored.snapshot([], lambda _name: ())
    assert restored.watermark_for(KEY) == first
    assert restored.watermark_for(ROLE) == 0
    assert counts == {KEY: 1}


def test_an_evicted_role_keeps_its_watermark_within_its_own_bound(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        seq = _chat(store, f"{ROLE},P/triage")
        tracker = MailboxPendingTracker(store, max_identities=1, max_role_watermarks=1)
        assert tracker.acknowledge("P/bob", seq, roles=[ROLE]) is True
        tracker.learn_roles(["P/triage"])
        assert tracker.known_identities == ("role:P/triage",)
        assert tracker.watermark_for(KEY) == seq
        tracker.learn_roles([ROLE])
        assert tracker.known_identities == (KEY,)
        assert tracker.watermark_for(KEY) == seq
        assert tracker.acknowledge("P/bob", seq, roles=["P/triage"]) is True
        tracker.learn_roles(["P/third"])
        tracker.learn_roles(["P/fourth"])
    assert tracker.watermark_for(KEY) == 0
    assert tracker.watermark_for("role:P/triage") == seq


def test_a_cold_role_count_is_projected_from_its_own_watermark(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        first = _chat(store, ROLE)
        _chat(store, ROLE)
        tracker = MailboxPendingTracker(store)
        assert tracker.acknowledge("P/bob", first, roles=[ROLE]) is True
        cold = MailboxPendingTracker(store)
        assert cold.pending_count(KEY) == 1


def test_a_new_role_mailbox_starts_where_its_plain_name_stood(tmp_path: Path) -> None:
    """Mail closed under the plain role name before an upgrade is not replayed to a holder."""
    with EventStore(tmp_path / "hub.db") as store:
        _chat(store, ROLE)
        closed = _chat(store, ROLE)
        later = _chat(store, ROLE)
        tracker = MailboxPendingTracker(store)
        assert tracker.advance(ROLE, closed, source="closed") is True
        tracker.learn_roles([ROLE])
        backlog = tracker.role_backlog([ROLE], limit=10)
        assert tracker.watermark_for(KEY) == closed
        assert tracker.pending_count(KEY) == 1
    assert [event.seq for event in backlog] == [later]


def test_restart_starts_the_role_at_the_higher_of_its_own_and_its_plain_mark(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hub.db"
    with EventStore(path) as store:
        first = _chat(store, ROLE)
        closed = _chat(store, ROLE)
        _chat(store, ROLE)
        store.append(
            EventKind.MAILBOX_WATERMARK,
            {"identity": ROLE, "through_seq": closed, "source": "closed"},
        )
        store.append(
            EventKind.MAILBOX_WATERMARK,
            {"identity": KEY, "through_seq": first, "source": "ack"},
        )
    with EventStore(path) as reopened:
        restored = MailboxPendingTracker(reopened)
        assert restored.watermark_for(KEY) == closed
        assert restored.pending_count(KEY) == 1
    with EventStore(tmp_path / "plain.db") as plain:
        _chat(plain, ROLE)
        mark = _chat(plain, ROLE)
        plain.append(
            EventKind.MAILBOX_WATERMARK,
            {"identity": ROLE, "through_seq": mark, "source": "closed"},
        )
        cold = MailboxPendingTracker(plain)
        cold.learn_roles([ROLE])
        assert cold.watermark_for(KEY) == mark


def test_a_role_that_already_had_a_mailbox_takes_nothing_from_its_plain_name(
    tmp_path: Path,
) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        acked = _chat(store, f"{ROLE},P/triage")
        later = _chat(store, ROLE)
        tracker = MailboxPendingTracker(store, max_identities=1)
        assert tracker.acknowledge("P/bob", acked, roles=[ROLE]) is True
        tracker.learn_roles(["P/triage"])
        assert KEY not in tracker.known_identities
        tracker.observe_chat(later, {"sender": "X/peer", "target": ROLE})
        assert tracker.advance(ROLE, later, source="cursor") is True
        tracker.learn_roles([ROLE])
    assert tracker.watermark_for(KEY) == acked


def test_a_binding_is_journalled_once_per_change_and_restored(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    with EventStore(path) as store:
        tracker = MailboxPendingTracker(store)
        assert tracker.bind_roles("P/bob", []) is False
        assert tracker.bind_roles("P/bob", [ROLE, " P/triage ", ROLE]) is True
        assert tracker.bind_roles("P/bob", ["P/triage", ROLE]) is False
        assert tracker.bind_roles("P/bob", ["P/triage"]) is True
        assert tracker.bind_roles("P/bob", []) is True
        assert tracker.bind_roles("  ", [ROLE]) is False
        bindings = [event.payload for event in store.read_since(0, kinds=(EventKind.ROLE_BINDING,))]
        _chat(store, ROLE)
    assert bindings == [
        {"identity": "P/bob", "roles": ["P/reviewer", "P/triage"]},
        {"identity": "P/bob", "roles": ["P/triage"]},
        {"identity": "P/bob", "roles": []},
    ]
    with EventStore(path) as reopened:
        reopened.append(EventKind.ROLE_BINDING, {"identity": "", "roles": [ROLE]})
        reopened.append(EventKind.ROLE_BINDING, {"identity": "P/eve", "roles": "P/x"})
        restored = MailboxPendingTracker(reopened)
        assert restored.has_role_mailbox(ROLE)
        assert restored.pending_count(KEY) == 1
        assert restored.bind_roles("P/bob", []) is False
        assert restored.bind_roles("P/eve", []) is False
    assert MailboxPendingTracker(None).bind_roles("P/bob", [ROLE]) is False


def test_remembered_bindings_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from synapse_channel.core import mailbox_pending

    monkeypatch.setattr(mailbox_pending, "DEFAULT_MAX_ROLE_BINDINGS", 1)
    with EventStore(tmp_path / "hub.db") as store:
        tracker = MailboxPendingTracker(store)
        assert tracker.bind_roles("P/a", [ROLE]) is True
        assert tracker.bind_roles("P/b", [ROLE]) is True
        assert tracker.bind_roles("P/a", [ROLE]) is True

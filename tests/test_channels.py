# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the private-channel membership registry

from __future__ import annotations

from typing import cast

from synapse_channel.core.channels import MAX_CHANNEL_ID_LENGTH, MAX_CHANNELS, ChannelRegistry


def test_create_makes_the_owner_the_first_member() -> None:
    registry = ChannelRegistry()

    ok, message = registry.create("project:release", owner="alice", label="Release")

    assert ok is True
    assert "created" in message
    assert registry.exists("project:release")
    assert registry.is_member("project:release", "alice")
    assert registry.members("project:release") == frozenset({"alice"})
    assert registry.owner("project:release") == "alice"


def test_create_rejects_blank_and_overlong_ids_and_duplicates() -> None:
    registry = ChannelRegistry()

    assert registry.create("   ", owner="alice")[0] is False
    assert registry.create("c", owner="  ")[0] is False
    assert registry.create("x" * (MAX_CHANNEL_ID_LENGTH + 1), owner="alice")[0] is False
    assert registry.create("c", owner="alice")[0] is True
    dup_ok, dup_msg = registry.create("c", owner="bob")
    assert dup_ok is False
    assert "already exists" in dup_msg


def test_create_is_bounded_by_max_channels() -> None:
    registry = ChannelRegistry(max_channels=2)

    assert registry.create("a", owner="o")[0] is True
    assert registry.create("b", owner="o")[0] is True
    full_ok, full_msg = registry.create("c", owner="o")
    assert full_ok is False
    assert "full" in full_msg


def test_non_finite_max_channels_falls_back_to_the_default() -> None:
    registry = ChannelRegistry(max_channels=cast(int, float("inf")))

    assert registry.max_channels == MAX_CHANNELS


def test_join_and_leave_change_membership() -> None:
    registry = ChannelRegistry()
    registry.create("c", owner="alice")
    registry.invite("c", "alice", "bob")

    assert registry.join("c", "bob")[0] is True
    assert registry.members("c") == frozenset({"alice", "bob"})
    # Idempotent join is reported as no-change.
    assert registry.join("c", "bob")[0] is False

    assert registry.leave("c", "bob")[0] is True
    assert registry.is_member("c", "bob") is False
    # Leaving when not a member is refused.
    assert registry.leave("c", "carol")[0] is False


def test_join_rejects_a_blank_member_name() -> None:
    registry = ChannelRegistry()
    registry.create("c", owner="alice")

    ok, message = registry.join("c", "   ")

    assert ok is False
    assert "invalid member" in message


def test_leave_drops_the_channel_when_the_last_member_leaves() -> None:
    registry = ChannelRegistry()
    registry.create("c", owner="alice")

    assert registry.leave("c", "alice")[0] is True
    assert registry.exists("c") is False
    assert registry.members("c") == frozenset()


def test_join_and_leave_refuse_unknown_channels() -> None:
    registry = ChannelRegistry()

    assert registry.join("nope", "bob")[0] is False
    assert registry.leave("nope", "bob")[0] is False
    assert registry.is_member("nope", "bob") is False
    assert registry.owner("nope") is None


def test_join_is_refused_without_an_invite() -> None:
    # F3 secure default: an agent can no longer self-join a private channel by id.
    registry = ChannelRegistry()
    registry.create("c", owner="alice")

    ok, message = registry.join("c", "bob")

    assert ok is False
    assert "not invited" in message
    assert registry.is_member("c", "bob") is False


def test_only_the_owner_may_invite() -> None:
    registry = ChannelRegistry()
    registry.create("c", owner="alice")
    registry.invite("c", "alice", "bob")
    registry.join("c", "bob")

    # A non-owner member cannot invite; only the creator controls the audience.
    ok, message = registry.invite("c", "bob", "carol")

    assert ok is False
    assert "only the owner may invite" in message
    assert registry.join("c", "carol")[0] is False


def test_invite_is_consumed_on_join_and_does_not_permit_rejoin() -> None:
    registry = ChannelRegistry()
    registry.create("c", owner="alice")
    registry.invite("c", "alice", "bob")

    assert registry.join("c", "bob")[0] is True
    registry.leave("c", "bob")
    # The invite was consumed by the first join; rejoining needs a fresh invite.
    ok, message = registry.join("c", "bob")

    assert ok is False
    assert "not invited" in message


def test_invite_refuses_existing_members_and_duplicate_invites() -> None:
    registry = ChannelRegistry()
    registry.create("c", owner="alice")

    assert registry.invite("c", "alice", "")[0] is False  # blank invitee
    assert registry.invite("c", "alice", "alice")[1].endswith("already a member of 'c'")
    assert registry.invite("nope", "alice", "bob")[0] is False  # unknown channel

    assert registry.invite("c", "alice", "bob")[0] is True
    ok, message = registry.invite("c", "alice", "bob")
    assert ok is False
    assert "already invited" in message


def test_channels_for_lists_membership_sorted() -> None:
    registry = ChannelRegistry()
    registry.create("b", owner="alice")
    registry.create("a", owner="alice")
    registry.invite("a", "alice", "bob")
    registry.join("a", "bob")

    assert registry.channels_for("alice") == ["a", "b"]
    assert registry.channels_for("bob") == ["a"]
    assert registry.channels_for("stranger") == []


def test_snapshot_is_sorted_and_json_friendly() -> None:
    registry = ChannelRegistry()
    registry.create("b", owner="alice", label="Bee")
    registry.create("a", owner="bob")
    registry.invite("a", "bob", "carol")
    registry.join("a", "carol")

    snapshot = registry.snapshot()

    assert [entry["channel_id"] for entry in snapshot] == ["a", "b"]
    assert snapshot[0]["members"] == ["bob", "carol"]
    assert snapshot[1]["label"] == "Bee"
    assert snapshot[1]["owner"] == "alice"


def test_channel_history_is_member_visible_and_bounded() -> None:
    registry = ChannelRegistry()
    registry.create("ops", owner="alice")
    registry.invite("ops", "alice", "bob")
    registry.join("ops", "bob")

    for msg_id in range(1, 4):
        registry.retain_message(
            "ops",
            {
                "type": "chat",
                "sender": "alice",
                "payload": f"note-{msg_id}",
                "msg_id": msg_id,
                "channel": "ops",
            },
            max_messages=2,
        )

    assert [item["payload"] for item in registry.history_for("ops", "bob")] == [
        "note-2",
        "note-3",
    ]
    assert registry.history_for("ops", "carol") == []
    assert registry.history_for("ops", "alice", limit=1)[0]["payload"] == "note-3"


def test_channel_history_non_finite_bounds_degrade_safely() -> None:
    registry = ChannelRegistry()
    registry.create("ops", owner="alice")
    registry.retain_message(
        "ops",
        {"type": "chat", "payload": "note-1", "channel": "ops"},
        max_messages=cast(int, float("inf")),
    )

    assert registry.history_for("ops", "alice", limit=cast(int, float("nan"))) == []


def test_channel_history_returns_copies() -> None:
    registry = ChannelRegistry()
    registry.create("ops", owner="alice")
    registry.retain_message("ops", {"payload": "original", "channel": "ops"}, max_messages=20)

    visible = registry.history_for("ops", "alice")
    visible[0]["payload"] = "mutated"

    assert registry.history_for("ops", "alice")[0]["payload"] == "original"


def test_retain_message_ignores_unknown_channel() -> None:
    registry = ChannelRegistry()

    registry.retain_message("missing", {"payload": "ignored"}, max_messages=20)

    assert registry.history_for("missing", "alice") == []

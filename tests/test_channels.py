# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the private-channel membership registry

from __future__ import annotations

from synapse_channel.core.channels import MAX_CHANNEL_ID_LENGTH, ChannelRegistry


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


def test_join_and_leave_change_membership() -> None:
    registry = ChannelRegistry()
    registry.create("c", owner="alice")

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


def test_channels_for_lists_membership_sorted() -> None:
    registry = ChannelRegistry()
    registry.create("b", owner="alice")
    registry.create("a", owner="alice")
    registry.join("a", "bob")

    assert registry.channels_for("alice") == ["a", "b"]
    assert registry.channels_for("bob") == ["a"]
    assert registry.channels_for("stranger") == []


def test_snapshot_is_sorted_and_json_friendly() -> None:
    registry = ChannelRegistry()
    registry.create("b", owner="alice", label="Bee")
    registry.create("a", owner="bob")
    registry.join("a", "carol")

    snapshot = registry.snapshot()

    assert [entry["channel_id"] for entry in snapshot] == ["a", "b"]
    assert snapshot[0]["members"] == ["bob", "carol"]
    assert snapshot[1]["label"] == "Bee"
    assert snapshot[1]["owner"] == "alice"

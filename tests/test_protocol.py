# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the on-wire message envelope builders

from __future__ import annotations

import json

import pytest

from synapse_channel.core.protocol import (
    MAX_JSON_DEPTH,
    RESOURCE_TYPE_ALIASES,
    SENDER_HUB,
    WIRE_PROTOCOL_VERSION,
    MessageType,
    _exceeds_json_depth,
    build_envelope,
    is_recipient,
    loads_bounded,
    read_protocol_version,
    system_message,
)


def test_message_type_values_are_stable_wire_strings() -> None:
    assert MessageType.CHAT == "chat"
    assert MessageType.CLAIM_GRANTED == "claim_granted"
    assert MessageType.DELIVERY_RECEIPT == "delivery_receipt"
    assert MessageType.NAME_CONFLICT == "name_conflict"
    assert MessageType.RESUME_REQUEST == "resume_request"
    assert MessageType.RESUME_SNAPSHOT == "resume_snapshot"
    assert MessageType.WAIT_REQUEST == "wait_request"
    assert MessageType.WAIT_DENIED == "wait_denied"


def test_resource_aliases_cover_accepted_inbound_types() -> None:
    assert RESOURCE_TYPE_ALIASES == {"resource", "resource_offer", "offer_resource"}


def test_build_envelope_defaults() -> None:
    msg = build_envelope("USER", MessageType.CHAT, payload="hi", now=1700.0)
    assert msg == {
        "sender": "USER",
        "target": "all",
        "type": "chat",
        "payload": "hi",
        "timestamp": 1700.0,
    }


def test_build_envelope_merges_extra_after_base_fields() -> None:
    msg = build_envelope(
        "A",
        MessageType.CLAIM,
        target="System",
        payload="T1",
        now=1.0,
        task_id="T1",
        note="work",
    )
    assert msg["target"] == "System"
    assert msg["task_id"] == "T1"
    assert msg["note"] == "work"


def test_build_envelope_uses_system_clock_when_now_is_none() -> None:
    before = __import__("time").time()
    msg = build_envelope("A", MessageType.HEARTBEAT)
    assert msg["timestamp"] >= before


def test_system_message_sets_hub_sender_and_id() -> None:
    msg = system_message(
        "Welcome",
        hub_id="syn-123",
        msg_type=MessageType.WELCOME,
        target="self",
        now=2.0,
        online_agents=["A"],
    )
    assert msg["sender"] == SENDER_HUB
    assert msg["hub_id"] == "syn-123"
    assert msg["type"] == "welcome"
    assert msg["target"] == "self"
    assert msg["timestamp"] == 2.0
    assert msg["online_agents"] == ["A"]


def test_system_message_defaults_to_system_type_and_broadcast() -> None:
    msg = system_message("note", hub_id="h", now=3.0)
    assert msg["type"] == "system"
    assert msg["target"] == "all"


def test_system_message_uses_system_clock_when_now_is_none() -> None:
    before = __import__("time").time()
    msg = system_message("note", hub_id="h")
    assert msg["timestamp"] >= before


def test_is_recipient_broadcast_and_empty() -> None:
    assert is_recipient("all", "B") is True
    assert is_recipient("", "B") is True


def test_is_recipient_single_and_several() -> None:
    assert is_recipient("B", "B") is True
    assert is_recipient("B", "C") is False
    assert is_recipient("B, C ,D", "C") is True
    assert is_recipient("B,C", "Z") is False


def test_is_recipient_glob_groups() -> None:
    assert is_recipient("quantum/*", "quantum/claude-7f3a")
    assert is_recipient("quantum/claude-*", "quantum/claude-7f3a")
    assert not is_recipient("quantum/*", "other/codex-1")
    assert is_recipient("quantum/*,other/codex-1", "other/codex-1")


def test_is_recipient_bare_project_reaches_subidentities() -> None:
    # A bare project target reaches a <project>/<id> agent for the INBOX (is_recipient): a
    # sole agent armed under a sub-identity must still receive project-addressed messages.
    assert is_recipient("quantum", "quantum/claude-7f3a")
    assert is_recipient("a,quantum", "quantum/claude-7f3a")
    # but it does not leak across projects or partial-prefix names
    assert not is_recipient("quantum", "other/codex-1")
    assert not is_recipient("quant", "quantum/claude-7f3a")
    assert not is_recipient("quantum-core", "quantum/claude-7f3a")
    # a bare name is unchanged
    assert is_recipient("quantum", "quantum")
    assert not is_recipient("quantum", "other")


def test_bare_project_does_not_wake_a_sub_seat_directed_only() -> None:
    """A bare-<project> message is a routine broadcast for a <project>/<seat> waiter.

    Regression for the multi-seat wake storm: convene traffic addressed to the bare project
    woke every seat. A bare project now *directs* (wakes) only the waiter armed as that
    project, not its sub-seats — though a sub-seat still *receives* it in its inbox, and a
    CEO or priority project message still wakes it. The inbox question (is_recipient) and the
    wake question (is_directed) are deliberately not the same matcher.
    """
    from synapse_channel.core.protocol import is_directed, wakes

    # the seat still RECEIVES bare-project traffic in its inbox
    assert is_recipient("quantum", "quantum/claude-7f3a")
    # but a bare-project message does NOT wake a sub-seat directed-only waiter
    assert not is_directed("quantum", "quantum/claude-7f3a")
    assert not wakes("quantum", "quantum/claude-7f3a", directed_only=True, sender="A")
    # a bare project inside a comma-list is still a broadcast for the seat
    assert not wakes("a,quantum", "quantum/claude-7f3a", directed_only=True, sender="A")
    # CEO or priority on a bare-project message still reaches and wakes the seat
    assert wakes("quantum", "quantum/claude-7f3a", directed_only=True, sender="CEO")
    assert wakes("quantum", "quantum/claude-7f3a", directed_only=True, priority=True)
    # the seat IS woken when named explicitly, or by a group glob it is in
    assert wakes("quantum/claude-7f3a", "quantum/claude-7f3a", directed_only=True, sender="A")
    assert wakes("quantum/*", "quantum/claude-7f3a", directed_only=True, sender="A")
    # a sole agent armed as the bare project still wakes on a bare-project message
    assert is_directed("quantum", "quantum")
    assert wakes("quantum", "quantum", directed_only=True, sender="A")


def test_is_directed_excludes_broadcast() -> None:
    from synapse_channel.core.protocol import is_directed

    assert is_directed("quantum/*", "quantum/claude-1")
    assert is_directed("B", "B")
    assert not is_directed("all", "quantum/claude-1")
    assert not is_directed("", "B")


def test_addresses_project() -> None:
    from synapse_channel.core.protocol import addresses_project

    assert addresses_project("all", "quantum")
    assert addresses_project("quantum", "quantum")
    assert addresses_project("quantum/claude-1", "quantum")
    assert addresses_project("quantum/*", "quantum")
    assert addresses_project("a,quantum/x", "quantum")
    assert not addresses_project("other/codex-1", "quantum")
    assert not addresses_project("quantum-core/x", "quantum")


def test_priority_senders_contains_ceo() -> None:
    from synapse_channel.core.protocol import PRIORITY_SENDERS

    assert "CEO" in PRIORITY_SENDERS


def test_wakes_normal_and_directed_only() -> None:
    from synapse_channel.core.protocol import wakes

    # normal mode: any recipient match, including a broadcast
    assert wakes("all", "B", directed_only=False, sender="A")
    assert wakes("B", "B", directed_only=False, sender="A")
    # directed-only: routine peer broadcast suppressed
    assert not wakes("all", "B", directed_only=True, sender="A")
    # directed-only: a directed message still wakes
    assert wakes("B", "B", directed_only=True, sender="A")
    assert wakes("quantum/*", "quantum/c-1", directed_only=True, sender="A")
    # directed-only: a priority broadcast wakes
    assert wakes("all", "B", directed_only=True, sender="A", priority=True)
    # directed-only: a CEO broadcast always wakes
    assert wakes("all", "B", directed_only=True, sender="CEO")


def test_wakes_priority_and_ceo_do_not_leak_to_unaddressed_waiters() -> None:
    """A priority/CEO message directed at one agent must not wake everyone else.

    Regression for the fleet-wide wake storm: ``priority`` and a ``PRIORITY_SENDERS``
    sender elevate a message that *reaches* the waiter (a broadcast or one addressed to
    it), not one directed elsewhere. Before the fix a single priority nudge to agent X
    woke every directed-only waiter on the bus, who then re-armed in a noisy loop.
    """
    from synapse_channel.core.protocol import wakes

    # priority does NOT override the recipient check: a priority chat to FUSION must
    # not wake an unaddressed SYNAPSE-CHANNEL waiter.
    assert not wakes("FUSION", "SYNAPSE-CHANNEL", directed_only=True, priority=True)
    # a CEO message directed at one agent does not wake a different one.
    assert not wakes("FUSION", "SYNAPSE-CHANNEL", directed_only=True, sender="CEO")
    # a priority message to a comma-list of others does not wake a non-member.
    assert not wakes("X,Y,Z", "SYNAPSE-CHANNEL", directed_only=True, priority=True)
    # a priority message to a group glob does not wake an agent outside the group.
    assert not wakes("quantum/*", "studio/c-1", directed_only=True, priority=True)

    # the elevation still works where the message genuinely reaches the waiter:
    assert wakes("all", "B", directed_only=True, priority=True)  # priority broadcast
    assert wakes("all", "B", directed_only=True, sender="CEO")  # CEO broadcast
    assert wakes("B", "B", directed_only=True, priority=True)  # priority, addressed to B
    assert wakes("quantum/*", "quantum/c-1", directed_only=True, sender="CEO")  # CEO to my group


# --- role-aware addressing ("answers-to") ------------------------------------


def test_is_recipient_reaches_a_held_role() -> None:
    # An identity that holds a role is an addressee of a message to that role, even
    # though the role name does not match its instance name.
    assert is_recipient("SC/coordinator", "SC/claude-2759", roles=("SC/coordinator",))
    # ...while an identity that does NOT hold the role is not addressed by it.
    assert not is_recipient("SC/coordinator", "SC/claude-2759")
    assert not is_recipient("SC/coordinator", "SC/fable-ui", roles=("SC/git",))


def test_is_recipient_role_matches_via_glob_and_several() -> None:
    assert is_recipient("SC/coord*", "SC/claude-2759", roles=("SC/coordinator",))
    assert is_recipient("other, SC/coordinator", "SC/claude-2759", roles=("SC/coordinator",))
    # multiple held roles: any one matching is enough
    assert is_recipient("SC/git", "SC/claude-2759", roles=("SC/coordinator", "SC/git"))
    # a glob resolves against the role's OWN namespace, not the holder's: with a name
    # that does not match the glob, only a role inside the glob's namespace matches.
    assert is_recipient("mon/*", "SC/claude-2759", roles=("mon/watcher",))
    assert not is_recipient("mon/*", "SC/claude-2759", roles=("SC/coordinator",))


def test_is_recipient_empty_roles_preserves_plain_matching() -> None:
    # The default empty roles must not change any existing name/project behaviour.
    assert is_recipient("SC/claude-2759", "SC/claude-2759", roles=())
    assert is_recipient("SC", "SC/claude-2759", roles=())
    assert not is_recipient("SC/other", "SC/claude-2759", roles=())


def test_is_directed_wakes_on_a_held_role() -> None:
    from synapse_channel.core.protocol import is_directed

    assert is_directed("SC/coordinator", "SC/claude-2759", roles=("SC/coordinator",))
    # a role is a DIRECTED target, so a directed-only waiter treats it as a wake
    assert not is_directed("SC/coordinator", "SC/claude-2759")
    # a bare project of the role still does not wake a sub-seat (anti-wake-storm holds)
    assert not is_directed("SC", "SC/claude-2759", roles=("SC/coordinator",))


def test_wakes_directed_only_wakes_on_held_role() -> None:
    """The coordinator regression: a directed-only waiter must wake on its role.

    A message addressed to ``SC/coordinator`` was silently dropped because the waiter
    armed as ``SC/claude-2759`` did not answer to the role — it neither woke nor
    surfaced in the inbox and the hub dead-lettered it. With the role bound, the
    directed-only waiter wakes promptly on a message to the role it holds.
    """
    from synapse_channel.core.protocol import wakes

    roles = ("SC/coordinator",)
    # the exact failure that was observed: role message now wakes the holder
    assert wakes("SC/coordinator", "SC/claude-2759", directed_only=True, sender="peer", roles=roles)
    # without the role bound it stays suppressed (unchanged legacy behaviour)
    assert not wakes("SC/coordinator", "SC/claude-2759", directed_only=True, sender="peer")
    # a role addressed to a DIFFERENT role does not wake this holder (no wake storm)
    assert not wakes("SC/git", "SC/claude-2759", directed_only=True, sender="peer", roles=roles)
    # normal (non-directed-only) mode also honours the role
    assert wakes(
        "SC/coordinator", "SC/claude-2759", directed_only=False, sender="peer", roles=roles
    )
    # the instance name still wakes regardless of roles
    assert wakes("SC/claude-2759", "SC/claude-2759", directed_only=True, sender="peer", roles=roles)


def test_wakes_role_does_not_leak_across_holders() -> None:
    from synapse_channel.core.protocol import wakes

    # two instances, only one holds the coordinator role
    assert wakes(
        "SC/coordinator",
        "SC/claude-2759",
        directed_only=True,
        sender="p",
        roles=("SC/coordinator",),
    )
    assert not wakes(
        "SC/coordinator", "SC/fable-ui", directed_only=True, sender="p", roles=("SC/git",)
    )


# --- bounded JSON decode -----------------------------------------------------


def test_loads_bounded_parses_normal_json() -> None:
    assert loads_bounded('{"a": [1, 2, {"b": 3}]}') == {"a": [1, 2, {"b": 3}]}


def test_loads_bounded_accepts_bytes() -> None:
    assert loads_bounded(b'{"x": 1}') == {"x": 1}


def test_loads_bounded_accepts_nesting_at_the_limit() -> None:
    payload = "[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH
    result = loads_bounded(payload)
    assert isinstance(result, list)


def test_loads_bounded_rejects_nesting_past_the_limit() -> None:
    payload = "[" * (MAX_JSON_DEPTH + 1) + "]" * (MAX_JSON_DEPTH + 1)
    with pytest.raises(json.JSONDecodeError, match="nested deeper"):
        loads_bounded(payload)


def test_loads_bounded_rejects_a_far_too_deep_frame_without_recursing() -> None:
    # Far past any interpreter recursion limit: the guard rejects, never recurses.
    payload = "[" * 5000 + "]" * 5000
    with pytest.raises(json.JSONDecodeError):
        loads_bounded(payload)


def test_loads_bounded_reraises_malformed_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        loads_bounded("{not json")


@pytest.mark.parametrize(
    "frame",
    [
        '{"x": NaN}',
        '{"x": Infinity}',
        '{"x": -Infinity}',
        "[NaN]",
        '{"a": {"b": Infinity}}',  # nested, still rejected
    ],
)
def test_loads_bounded_rejects_non_finite_constants(frame: str) -> None:
    # RFC 8259 has no NaN/Infinity; json.loads accepts the tokens by default. A
    # non-finite float breaks downstream ordering (nan) and int()/float() conversions,
    # so the single decode boundary rejects it as malformed — a defence in depth
    # beneath the per-field guards. The hub never emits one, so no legitimate frame loses.
    with pytest.raises(json.JSONDecodeError, match="non-finite JSON constant"):
        loads_bounded(frame)


def test_loads_bounded_keeps_finite_floats() -> None:
    # The rejection must not touch ordinary finite numbers, including exponents.
    assert loads_bounded('{"ts": 123.5, "neg": -3.2, "big": 1e10, "n": 42}') == {
        "ts": 123.5,
        "neg": -3.2,
        "big": 1e10,
        "n": 42,
    }


def test_exceeds_json_depth_ignores_brackets_inside_strings() -> None:
    # A string value packed with brackets is structurally shallow.
    assert _exceeds_json_depth('{"k": "[[[[[[[[]]]]]]]]"}', 2) is False


def test_exceeds_json_depth_honours_escaped_quotes() -> None:
    # The escaped quote does not end the string, so the bracket run stays quoted.
    assert _exceeds_json_depth('{"k": "a\\"[[[", "m": 1}', 2) is False


def test_exceeds_json_depth_counts_real_nesting() -> None:
    assert _exceeds_json_depth("[[[[]]]]", 3) is True
    assert _exceeds_json_depth("[[[[]]]]", 4) is False


def test_wire_protocol_version_is_a_positive_integer() -> None:
    # A stable compatibility signal, not a release counter — a plain positive int.
    assert isinstance(WIRE_PROTOCOL_VERSION, int)
    assert not isinstance(WIRE_PROTOCOL_VERSION, bool)
    assert WIRE_PROTOCOL_VERSION >= 1


def test_read_protocol_version_accepts_a_plain_integer() -> None:
    assert read_protocol_version(1) == 1
    assert read_protocol_version(7) == 7


@pytest.mark.parametrize("value", [None, True, False, 1.0, "1", "", [1], {"v": 1}])
def test_read_protocol_version_rejects_non_integer_or_boolean(value: object) -> None:
    # An absent field (None), a bool (an int subclass but not a version), or any
    # non-int degrades to None rather than a spurious version a check might act on.
    assert read_protocol_version(value) is None

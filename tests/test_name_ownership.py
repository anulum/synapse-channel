# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — name-ownership lease table unit surface

"""The hub's name→lease table: grant, verification, and offline expiry.

Every test drives the real :class:`NameOwnership` with a deterministic clock —
the clock is a designed injection seam (the hub passes ``time.monotonic``), not
a stand-in for behaviour. Expiry is therefore pinned exactly at its boundary
instead of sampled by sleeping.
"""

from __future__ import annotations

from synapse_channel.core.name_ownership import DEFAULT_LEASE_OFFLINE_TTL, NameOwnership

NAME = "PROJ/agent-1"


class SteppingClock:
    """A monotonic clock the test advances explicitly."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_a_granted_lease_verifies_only_its_own_token() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock)
    token = table.grant(NAME)
    assert table.is_leased(NAME)
    assert table.matches(NAME, token)
    assert not table.matches(NAME, token + "x")
    assert not table.matches(NAME, "")
    assert not table.matches("PROJ/agent-2", token)


def test_the_token_is_returned_once_and_never_stored_in_plaintext() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock)
    token = table.grant(NAME)
    # The table keeps only digests: the plaintext token appears nowhere in the
    # instance state, so neither a log line nor a repr can leak it.
    state = repr(vars(table)) + repr(table._token_digests)
    assert token not in state


def test_a_regrant_invalidates_the_previous_token() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock)
    old = table.grant(NAME)
    new = table.grant(NAME)
    assert not table.matches(NAME, old)
    assert table.matches(NAME, new)


def test_the_lease_never_lapses_while_its_holder_is_online() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock, offline_ttl=10.0)
    token = table.grant(NAME)
    clock.now += 1_000_000.0
    assert table.is_leased(NAME)
    assert table.matches(NAME, token)


def test_the_lease_survives_a_disconnect_shorter_than_the_ttl() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock, offline_ttl=10.0)
    token = table.grant(NAME)
    table.mark_offline(NAME)
    clock.now += 9.999
    assert table.is_leased(NAME)
    assert table.matches(NAME, token)


def test_the_lease_lapses_exactly_at_the_offline_ttl_boundary() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock, offline_ttl=10.0)
    token = table.grant(NAME)
    table.mark_offline(NAME)
    clock.now += 10.0
    assert not table.is_leased(NAME)
    assert not table.matches(NAME, token)
    # A lapsed lease is gone, not dormant: coming back online cannot revive it.
    table.mark_online(NAME)
    assert not table.is_leased(NAME)


def test_a_reconnect_freezes_the_expiry_window() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock, offline_ttl=10.0)
    token = table.grant(NAME)
    table.mark_offline(NAME)
    clock.now += 9.0
    table.mark_online(NAME)
    clock.now += 1_000.0
    assert table.matches(NAME, token)


def test_the_expiry_window_runs_from_the_first_disconnect() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock, offline_ttl=10.0)
    table.grant(NAME)
    table.mark_offline(NAME)
    clock.now += 9.0
    # A second offline stamp without an intervening bind must not restart the
    # window, or a periodic reaper touching the name would keep it leased forever.
    table.mark_offline(NAME)
    clock.now += 1.0
    assert not table.is_leased(NAME)


def test_a_zero_ttl_collapses_the_lease_to_the_connection_lifetime() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock, offline_ttl=0.0)
    token = table.grant(NAME)
    assert table.matches(NAME, token)
    table.mark_offline(NAME)
    assert not table.is_leased(NAME)


def test_a_negative_ttl_is_clamped_and_a_junk_ttl_takes_the_default() -> None:
    clock = SteppingClock()
    assert NameOwnership(clock=clock, offline_ttl=-5.0).offline_ttl == 0.0
    junk = NameOwnership(clock=clock, offline_ttl=float("nan"))
    assert junk.offline_ttl == DEFAULT_LEASE_OFFLINE_TTL


def test_release_returns_the_name_to_first_come_first_owned() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock)
    token = table.grant(NAME)
    table.release(NAME)
    assert not table.is_leased(NAME)
    assert not table.matches(NAME, token)
    # Releasing an unknown name is a no-op, never an error.
    table.release("PROJ/never-leased")


def test_marks_on_an_unleased_name_are_no_ops() -> None:
    clock = SteppingClock()
    table = NameOwnership(clock=clock, offline_ttl=10.0)
    table.mark_offline(NAME)
    table.mark_online(NAME)
    assert not table.is_leased(NAME)
    # An offline stamp recorded before any grant must not have leaked in.
    table.grant(NAME)
    clock.now += 1_000.0
    assert table.is_leased(NAME)

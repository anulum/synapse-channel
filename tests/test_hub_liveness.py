# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — liveness query view over the reaction store and live roster

from __future__ import annotations

import time
from typing import Any

from synapse_channel.core.agent_liveness import RecipientLiveness
from synapse_channel.core.hub_liveness import HubLivenessView


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _view(
    *,
    enabled: bool = True,
    waiter_window: float = 20.0,
    recipient_window: float = 10.0,
    clock: _Clock | None = None,
) -> tuple[HubLivenessView, RecipientLiveness, dict[str, Any], dict[str, float]]:
    """Build a view over a fresh reaction store, socket registry, and last-seen map."""
    reactions = RecipientLiveness(window_seconds=recipient_window)
    sockets: dict[str, Any] = {}
    last_seen: dict[str, float] = {}
    view = HubLivenessView(
        reactions,
        enabled=enabled,
        waiter_window_seconds=waiter_window,
        online_agents=lambda: sorted(sockets),
        agent_sockets=sockets,
        last_seen=last_seen,
        clock=clock or _Clock(),
    )
    return view, reactions, sockets, last_seen


class TestHasLiveWaiter:
    def test_no_sidecar_is_not_a_live_waiter(self) -> None:
        view, _reactions, sockets, _seen = _view()
        sockets["BETA"] = object()

        assert view.has_live_waiter("BETA") is False

    def test_a_fresh_keepalive_is_a_live_waiter(self) -> None:
        view, _reactions, sockets, seen = _view(waiter_window=20.0)
        sockets["BETA-rx"] = object()
        seen["BETA-rx"] = time.time()

        assert view.has_live_waiter("BETA") is True

    def test_a_silent_sidecar_is_not_a_live_waiter(self) -> None:
        view, _reactions, sockets, seen = _view(waiter_window=20.0)
        sockets["BETA-rx"] = object()
        seen["BETA-rx"] = time.time() - 100.0

        assert view.has_live_waiter("BETA") is False

    def test_a_sidecar_never_stamped_is_not_a_live_waiter(self) -> None:
        view, _reactions, sockets, _seen = _view()
        sockets["BETA-rx"] = object()  # present but no last-seen

        assert view.has_live_waiter("BETA") is False

    def test_freshness_is_independent_of_the_enabled_flag(self) -> None:
        view, _reactions, sockets, seen = _view(enabled=False)
        sockets["BETA-rx"] = object()
        seen["BETA-rx"] = time.time()

        assert view.has_live_waiter("BETA") is True


class TestRecipientsWithoutLiveWaiter:
    def test_disabled_flags_nobody(self) -> None:
        view, reactions, sockets, _seen = _view(enabled=False)
        sockets["BETA"] = object()
        reactions.touch("BETA", 0.0)

        assert view.recipients_without_live_waiter(["BETA"]) == ()

    def test_a_stale_recipient_without_a_waiter_is_flagged(self) -> None:
        clock = _Clock()
        view, reactions, sockets, _seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        reactions.touch("BETA", 0.0)
        clock.t = 20.0

        assert view.recipients_without_live_waiter(["BETA"]) == ("BETA",)

    def test_a_recipient_with_no_record_is_flagged(self) -> None:
        clock = _Clock()
        view, _reactions, sockets, _seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        clock.t = 3.0

        assert view.recipients_without_live_waiter(["BETA"]) == ("BETA",)

    def test_a_live_waiter_suppresses_the_flag(self) -> None:
        clock = _Clock()
        view, reactions, sockets, seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        sockets["BETA-rx"] = object()
        seen["BETA-rx"] = time.time()  # fresh
        reactions.touch("BETA", 0.0)
        clock.t = 20.0  # stale by reaction, but the live waiter vouches

        assert view.recipients_without_live_waiter(["BETA"]) == ()

    def test_a_stale_waiter_socket_no_longer_suppresses(self) -> None:
        clock = _Clock()
        view, reactions, sockets, seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        sockets["BETA-rx"] = object()
        seen["BETA-rx"] = time.time() - 100.0  # keepalives stopped
        reactions.touch("BETA", 0.0)
        clock.t = 20.0

        assert view.recipients_without_live_waiter(["BETA"]) == ("BETA",)

    def test_a_recent_reaction_suppresses_the_flag(self) -> None:
        clock = _Clock()
        view, reactions, sockets, _seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        reactions.touch("BETA", 15.0)
        clock.t = 20.0  # only 5s since the reaction

        assert view.recipients_without_live_waiter(["BETA"]) == ()

    def test_only_the_stale_recipients_are_returned_in_order(self) -> None:
        clock = _Clock()
        view, reactions, sockets, _seen = _view(recipient_window=10.0, clock=clock)
        for name in ("A", "B", "C"):
            sockets[name] = object()
        reactions.touch("A", 0.0)  # stale
        reactions.touch("B", 18.0)  # fresh
        # C never reacted → stale
        clock.t = 20.0

        assert view.recipients_without_live_waiter(["A", "B", "C"]) == ("A", "C")


class TestStaleOwnerReclaimable:
    def test_recovery_ttl_starts_when_the_reaction_window_expires(self) -> None:
        clock = _Clock()
        view, reactions, sockets, _seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        reactions.touch("BETA", 0.0)

        clock.t = 5.0
        assert view.stale_owner_reclaimable("BETA", ttl_seconds=10.0) is False
        clock.t = 19.0
        assert view.stale_owner_reclaimable("BETA", ttl_seconds=10.0) is False
        clock.t = 20.0
        assert view.stale_owner_reclaimable("BETA", ttl_seconds=10.0) is True

    def test_live_waiter_blocks_stale_socket_recovery(self) -> None:
        clock = _Clock()
        view, reactions, sockets, seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        sockets["BETA-rx"] = object()
        seen["BETA-rx"] = time.time()
        reactions.touch("BETA", 0.0)
        clock.t = 100.0

        assert view.stale_owner_reclaimable("BETA", ttl_seconds=10.0) is False

    def test_unknown_or_disabled_reaction_history_refuses_recovery(self) -> None:
        clock = _Clock()
        enabled, _reactions, sockets, _seen = _view(clock=clock)
        disabled, disabled_reactions, _, _ = _view(enabled=False, clock=clock)
        sockets["BETA"] = object()
        disabled_reactions.touch("BETA", 0.0)
        clock.t = 100.0

        assert enabled.stale_owner_reclaimable("BETA", ttl_seconds=0.0) is False
        assert disabled.stale_owner_reclaimable("BETA", ttl_seconds=0.0) is False


class TestRosterLiveness:
    def test_disabled_returns_empty(self) -> None:
        view, _reactions, sockets, _seen = _view(enabled=False)
        sockets["BETA"] = object()

        assert view.roster_liveness() == {}

    def test_a_recent_reaction_is_proven_live_with_its_age(self) -> None:
        clock = _Clock()
        view, reactions, sockets, _seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        reactions.touch("BETA", 5.0)
        clock.t = 10.0

        assert view.roster_liveness()["BETA"] == {
            "proven_live": True,
            "has_live_waiter": False,
            "last_reaction_age": 5.0,
        }

    def test_a_live_waiter_is_proven_live_even_when_stale(self) -> None:
        clock = _Clock()
        view, reactions, sockets, seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        sockets["BETA-rx"] = object()
        seen["BETA-rx"] = time.time()
        reactions.touch("BETA", 0.0)
        clock.t = 100.0

        info = view.roster_liveness()["BETA"]
        assert info["proven_live"] is True
        assert info["has_live_waiter"] is True

    def test_a_deaf_agent_is_not_proven_live_and_reports_its_silence(self) -> None:
        clock = _Clock()
        view, reactions, sockets, _seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()
        reactions.touch("BETA", 0.0)
        clock.t = 50.0

        assert view.roster_liveness()["BETA"] == {
            "proven_live": False,
            "has_live_waiter": False,
            "last_reaction_age": 50.0,
        }

    def test_an_agent_that_never_reacted_has_a_none_age(self) -> None:
        clock = _Clock()
        view, _reactions, sockets, _seen = _view(recipient_window=10.0, clock=clock)
        sockets["BETA"] = object()  # present, never touched
        clock.t = 3.0

        assert view.roster_liveness()["BETA"] == {
            "proven_live": False,
            "has_live_waiter": False,
            "last_reaction_age": None,
        }

    def test_waiter_sidecars_are_not_annotated_as_agents(self) -> None:
        view, _reactions, sockets, _seen = _view()
        sockets["BETA"] = object()
        sockets["BETA-rx"] = object()

        result = view.roster_liveness()
        assert "BETA" in result
        assert "BETA-rx" not in result

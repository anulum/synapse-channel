# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — who/state rendering edges: unknown wake, quiet me, observed peers
"""Render undeclared wake capability, a me-view without mailbox and observed-claim edges."""

from __future__ import annotations

import pytest

from synapse_channel.cli_query_rendering import _render_state, _render_who, _render_who_me
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_fold import fold_observed_state
from synapse_channel.core.multihub_merge import HubEvent
from synapse_channel.core.wake_capability import WAKE_DIRECT, WAKE_UNKNOWN
from synapse_channel.observed_peers import ObservedPeerSnapshot


def test_who_marks_only_declared_wake_capabilities(capsys: pytest.CaptureFixture[str]) -> None:
    """An undeclared or unknown capability adds no marker; a declared one is labelled."""
    _render_who(
        ["P/direct", "P/unknown", "P/absent"],
        wake_capabilities={"P/direct": WAKE_DIRECT, "P/unknown": WAKE_UNKNOWN},
    )
    lines = capsys.readouterr().out.splitlines()
    assert "  P/direct  (direct agent)" in lines
    assert "  P/unknown" in lines
    assert "  P/absent" in lines


def test_me_view_without_mailbox_flag_prints_no_mailbox_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --mailbox-pending the me-view ends after its wake-loop note."""
    _render_who_me(["P/me"], name="P/me", mailbox_pending={"P/me": 3})
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == (
        "  note: presence is not a wake loop; the waiter is what wakes quiet terminals."
    )
    assert "mailbox pending" not in out


def _claim(task_id: str, owner: str) -> HubEvent:
    return HubEvent(
        "east", 1, 1.0, EventKind.CLAIM, {"task_id": task_id, "owner": owner, "paths": ["a.py"]}
    )


def test_state_lists_unreachable_peers_and_filters_observed_claims_by_owner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unreachable peers are named with their error; another owner's observed claim is hidden."""
    east = ObservedPeerSnapshot(
        hub_id="east",
        uri="ws://east",
        reachable=True,
        cursor=2,
        state=fold_observed_state(
            [
                _claim("MINE", "team/agent"),
                HubEvent(
                    "east",
                    2,
                    2.0,
                    EventKind.CLAIM,
                    {"task_id": "THEIRS", "owner": "other/agent", "paths": ["b.py"]},
                ),
            ]
        ),
    )
    west = ObservedPeerSnapshot(hub_id="west", uri="ws://west", reachable=False, error="refused")
    north = ObservedPeerSnapshot(hub_id="north", uri="ws://north", reachable=False)

    _render_state({"active_claims": []}, owner="team", observed_peers=(east, west, north))

    lines = capsys.readouterr().out.splitlines()
    assert lines[-4:] == [
        "Observed claims (3; advisory, never local grants):",
        "  MINE [observed@east] owner=team/agent paths=a.py",
        "  observed@west unreachable: refused",
        "  observed@north unreachable: fetch failed",
    ]

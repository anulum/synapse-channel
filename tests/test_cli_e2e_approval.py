# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journeys for the approval workflow: request, decide, and replay.

``approval`` records advisory sign-off evidence on the shared log: one agent
requests approval for a subject, another approves or rejects it, and the state is
replayed offline from the hub's event store. It is an audit trail, not a runtime
gate — the journeys assert exactly that framing alongside the recorded verdicts.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli_e2e_helpers import isolated_hub, run_cli


def test_request_then_approve_is_replayed_from_the_store(tmp_path: Path) -> None:
    """A requested subject approved by another agent replays as approved."""
    with isolated_hub(tmp_path) as hub:
        requested = run_cli(
            "approval",
            "request",
            "--name",
            "ALPHA",
            "--subject",
            "gate-1",
            "--reason",
            "ship it",
            uri=hub.uri,
        )
        assert requested.ok(), requested.output

        decided = run_cli(
            "approval",
            "decide",
            "--name",
            "REVIEWER",
            "--subject",
            "gate-1",
            "--approve",
            "--reason",
            "looks right",
            uri=hub.uri,
        )
        assert decided.ok(), decided.output

        status = run_cli("approval", "status", str(hub.db_path), "--subject", "gate-1")
        assert status.ok(), status.output
        assert "gate-1: approved" in status.stdout
        assert "approved by REVIEWER" in status.stdout
        # The advisory framing must never read as a hard runtime gate.
        assert "not a runtime gate" in status.stdout


def test_request_then_reject_is_replayed_as_rejected(tmp_path: Path) -> None:
    """A rejected subject replays with the rejecting agent and reason."""
    with isolated_hub(tmp_path) as hub:
        run_cli("approval", "request", "--name", "A", "--subject", "gate-2", uri=hub.uri)
        rejected = run_cli(
            "approval",
            "decide",
            "--name",
            "B",
            "--subject",
            "gate-2",
            "--reject",
            "--reason",
            "unsafe",
            uri=hub.uri,
        )
        assert rejected.ok(), rejected.output

        status = run_cli("approval", "status", str(hub.db_path), "--subject", "gate-2", "--json")
        assert status.ok(), status.output
        payload = json.loads(status.stdout)
        entry = next(s for s in payload["statuses"] if s["current_state"] == "rejected")
        assert entry["decided_by"] == "B"
        assert "not a runtime gate" in payload["note"]


def test_pending_filter_empties_once_every_subject_is_decided(tmp_path: Path) -> None:
    """``--pending`` lists only awaiting subjects; a decided one drops off it."""
    with isolated_hub(tmp_path) as hub:
        run_cli("approval", "request", "--name", "A", "--subject", "gate-3", uri=hub.uri)

        pending_before = run_cli("approval", "status", str(hub.db_path), "--pending")
        assert pending_before.ok(), pending_before.output
        assert "gate-3" in pending_before.stdout

        run_cli(
            "approval", "decide", "--name", "B", "--subject", "gate-3", "--approve", uri=hub.uri
        )
        pending_after = run_cli("approval", "status", str(hub.db_path), "--pending")
        assert pending_after.ok(), pending_after.output
        assert "gate-3" not in pending_after.stdout

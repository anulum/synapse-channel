# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a bound review finding without a decision asks for one
"""Attention asks for an independent decision while a bound finding awaits one."""

from __future__ import annotations

import json
from pathlib import Path

from synapse_channel.core.persistence import EventStore
from test_cli_review_feedback import REVIEWER, _cli, _prepare


def test_bound_finding_without_a_decision_requests_an_independent_one(tmp_path: Path) -> None:
    """Before any approval, the open alert is a warning that names the missing decision."""
    _repo, _commit, review_store, _subject = _prepare(tmp_path)
    hub_db = tmp_path / "hub.db"
    EventStore(hub_db).close()
    queue = tmp_path / "attention" / "queue.db"
    sync = _cli(
        "attention",
        "sync",
        str(hub_db),
        "--review-store",
        str(review_store),
        "--reviewer-seat",
        REVIEWER,
        "--store",
        str(queue),
    )
    assert sync.returncode == 0, sync.stderr
    assert json.loads(sync.stdout)["synced"]["review"]["created"] == 1
    listed = _cli("attention", "list", "--store", str(queue), "--json")
    assert listed.returncode == 0, listed.stderr
    reviews = [
        row for row in json.loads(listed.stdout)["alerts"] if row["kind"] == "review_feedback"
    ]
    assert [(row["severity"], row["state"], row["action"]) for row in reviews] == [
        ("warning", "open", "Request an independent decision on the exact review")
    ]

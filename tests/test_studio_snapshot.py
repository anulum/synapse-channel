# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio snapshot projection regressions

from __future__ import annotations

from synapse_channel.studio_snapshot import (
    STUDIO_SNAPSHOT_PATH,
    UNKNOWN_VERDICT,
    build_studio_snapshot,
    frozen_studio_snapshot,
)

_DASHBOARD = {
    "board": {
        "tasks": [
            {"task_id": "t1", "title": "ready", "status": "open"},
            {"task_id": "t2", "title": "working", "status": "in_progress"},
            {"task_id": "t3", "title": "blocked", "status": "blocked", "depends_on": ["t2"]},
        ],
        "ready": ["t1"],
    },
    "state": {
        "active_claims": [
            {
                "task_id": "t2",
                "owner": "A/claude-1",
                "status": "working",
                "lease_expires_at": 1782760100.0,
                "paths": ["src/"],
            }
        ],
        "generated_at": 1782760000.0,
    },
    "fleet": {
        "agents": {
            "live": ["A/claude-1", "B/codex-2"],
            "waiters": ["A/claude-1"],
            "missing_waiters": ["C/claude-3"],
        },
        "claims": {
            "active": 2,
            "stale": 1,
            "active_claims": [{"owner": "A/claude-1", "scope": "src/"}],
            "stale_claims": [{"owner": "C/claude-3", "scope": "tests/"}],
        },
        "tasks": {"ready": ["t1", "t2"], "blocked": [{"task_id": "t3", "depends_on": ["t2"]}]},
        "task_graph": {"nodes": 3, "edges": 1},
        "branch_conflicts": [{"path": "src/x.py", "owners": ["A/claude-1", "B/codex-2"]}],
        "generated_at": 1782760000.0,
    },
    "risk": {
        "level": "amber",
        "signals": [
            {"level": "amber", "category": "waiter", "subject": "C/claude-3", "detail": "x"}
        ],
        "safe_next_work": ["t1", "t2"],
    },
}


def test_path_constant() -> None:
    assert STUDIO_SNAPSHOT_PATH == "/studio.json"


def test_projection_foregrounds_the_verdict_and_sections() -> None:
    studio = build_studio_snapshot(_DASHBOARD)
    assert studio["verdict"] == "amber"
    assert studio["risk"]["level"] == "amber"
    assert studio["generated_at"] == 1782760000.0
    assert set(studio) == {
        "verdict",
        "generated_at",
        "hub",
        "headline",
        "agents",
        "claims",
        "tasks",
        "conflicts",
        "security_posture",
        "observed_fleet",
        "risk",
    }
    assert studio["observed_fleet"]["configured"] is False
    assert studio["headline"]["peers_total"] == 0
    assert studio["agents"]["live"] == ["A/claude-1", "B/codex-2"]
    assert studio["hub"] == {"id": "", "version": "", "config_epoch": ""}
    assert studio["claims"]["active"] == [{"owner": "A/claude-1", "scope": "src/"}]
    assert studio["tasks"]["graph"] == {"nodes": 3, "edges": 1}
    columns = studio["tasks"]["columns"]
    assert columns["declared_tasks"] == 3
    assert [column["id"] for column in columns["columns"]] == [
        "open",
        "claimed",
        "working",
        "input_required",
        "blocked",
        "closed",
        "other",
    ]
    assert studio["risk"]["safe_next_work"] == ["t1", "t2"]
    assert studio["security_posture"]["level"] == "amber"


def test_headline_counts_are_derived_from_the_section_lists() -> None:
    studio = build_studio_snapshot(_DASHBOARD)
    headline = studio["headline"]
    # every count matches the length of the list the panels render — they cannot drift apart
    assert headline["agents_live"] == len(studio["agents"]["live"]) == 2
    assert headline["waiters_missing"] == len(studio["agents"]["missing_waiters"]) == 1
    assert headline["tasks_ready"] == len(studio["tasks"]["ready"]) == 2
    assert headline["tasks_blocked"] == len(studio["tasks"]["blocked"]) == 1
    assert headline["branch_conflicts"] == len(studio["conflicts"]) == 1
    assert headline["risk_signals"] == len(studio["risk"]["signals"]) == 1
    # active/stale are integer counts carried straight from the fleet view
    assert headline["claims_active"] == 2 and headline["claims_stale"] == 1


def test_empty_payload_projects_to_safe_defaults() -> None:
    studio = build_studio_snapshot({})
    assert studio["verdict"] == UNKNOWN_VERDICT
    assert studio["generated_at"] is None
    assert all(count == 0 for count in studio["headline"].values())
    assert studio["agents"] == {"live": [], "waiters": [], "missing_waiters": []}
    assert studio["claims"] == {"active": [], "stale": []}
    assert studio["tasks"]["graph"] is None
    assert studio["tasks"]["columns"]["total_cards"] == 0
    assert studio["security_posture"]["level"] == "amber"


def test_malformed_sections_are_coerced_not_raised() -> None:
    studio = build_studio_snapshot(
        {"fleet": {"agents": "nope", "claims": {"active": True}}, "risk": "nope"}
    )
    assert studio["verdict"] == UNKNOWN_VERDICT
    assert studio["agents"]["live"] == []
    assert studio["headline"]["claims_active"] == 0  # a bool is not a valid integer count


def test_frozen_snapshot_is_a_valid_representative_sample() -> None:
    studio = frozen_studio_snapshot()
    assert studio["verdict"] == "amber"
    assert studio["hub"]["id"] == "studio-demo"
    assert studio["hub"]["version"] == "0.98.21"
    assert studio["headline"]["agents_live"] == 2
    assert studio["security_posture"]["level"] == "green"
    assert studio["observed_fleet"]["configured"] is True
    assert studio["observed_fleet"]["peers_total"] == 1
    assert studio["headline"]["peers_reachable"] == 1
    assert studio["tasks"]["columns"]["declared_tasks"] == 3
    assert studio["tasks"]["columns"]["ad_hoc_claims"] == 1
    assert frozen_studio_snapshot() == studio  # deterministic


def test_observed_fleet_projects_from_dashboard_peers() -> None:
    studio = build_studio_snapshot(
        {
            **_DASHBOARD,
            "observed_peers": [
                {"hub_id": "soak", "reachable": False, "error": "timeout"},
            ],
        }
    )
    assert studio["observed_fleet"]["level"] == "red"
    assert studio["headline"]["peers_total"] == 1
    assert studio["headline"]["peers_reachable"] == 0

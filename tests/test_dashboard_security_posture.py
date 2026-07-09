# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio security-posture projection regressions

from __future__ import annotations

from typing import Any

from synapse_channel.dashboard_security_posture import build_security_posture


def _rows(posture: dict[str, object]) -> dict[str, dict[str, Any]]:
    """Index posture rows by surface name for compact assertions."""
    rows = posture["rows"]
    assert isinstance(rows, list)
    return {str(row["surface"]): row for row in rows if isinstance(row, dict)}


def test_security_posture_reports_current_evidence() -> None:
    posture = build_security_posture(
        {
            "config_epoch": "sha256:abc",
            "agent_roles": {"SYNAPSE-CHANNEL/operator": ["SYNAPSE-CHANNEL/admin"]},
            "manifest": [{"agent": "worker", "skills": ["sandbox"], "task_classes": ["wasm"]}],
            "observed_peers": [{"hub_id": "ml350", "reachable": True}],
            "fleet": {
                "receipts": [
                    {
                        "task_id": "release",
                        "author": "operator",
                        "text": "release receipt: evidence=pytest",
                    }
                ]
            },
        }
    )

    assert posture["level"] == "green"
    assert posture["counts"] == {"green": 5, "amber": 0, "red": 0, "unknown": 0}
    rows = _rows(posture)
    assert rows["exposure guard"]["state"] == "pinned"
    assert rows["ACL and roles"]["state"] == "role-bound"
    assert rows["sandbox grants"]["state"] == "advertised"
    assert rows["signed federation"]["state"] == "observed"
    assert rows["receipts"]["state"] == "evidenced"


def test_security_posture_stays_honest_when_evidence_is_absent() -> None:
    posture = build_security_posture({})

    assert posture["level"] == "amber"
    rows = _rows(posture)
    assert rows["exposure guard"]["state"] == "unreported"
    assert rows["ACL and roles"]["state"] == "no-live-role-bindings"
    assert rows["sandbox grants"]["state"] == "available"
    assert rows["signed federation"]["state"] == "available-local-only"
    assert rows["receipts"]["state"] == "no-current-receipts"


def test_security_posture_flags_configured_unreachable_federation() -> None:
    posture = build_security_posture({"observed_peers": [{"hub_id": "ml350", "reachable": False}]})

    assert posture["level"] == "red"
    assert _rows(posture)["signed federation"]["state"] == "configured-unreachable"


def test_security_posture_handles_partial_and_malformed_live_evidence() -> None:
    posture = build_security_posture(
        {
            "agent_roles": {"empty": [], "malformed": "operator"},
            "manifest": [
                {"skills": "sandbox", "task_classes": ["build"]},
                {"meta": {"runtime": "WASM runner"}},
            ],
            "observed_peers": [
                {"hub_id": "east", "reachable": True},
                {"hub_id": "west", "reachable": False},
            ],
        }
    )

    rows = _rows(posture)
    assert rows["ACL and roles"]["state"] == "no-live-role-bindings"
    assert rows["sandbox grants"]["state"] == "advertised"
    assert rows["signed federation"]["state"] == "partially-observed"

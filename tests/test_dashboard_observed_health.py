# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio observed-fleet health projection tests

from __future__ import annotations

from synapse_channel.dashboard_observed_health import (
    AMBER,
    GREEN,
    RED,
    build_observed_fleet_health,
)


def test_empty_observed_peers_is_amber_not_configured() -> None:
    health = build_observed_fleet_health({})
    assert health["configured"] is False
    assert health["level"] == AMBER
    assert health["peers_total"] == 0
    assert health["peers"] == []
    assert "--observed-peer" in health["detail"]


def test_all_reachable_peers_are_green() -> None:
    health = build_observed_fleet_health(
        {
            "observed_peers": [
                {
                    "hub_id": "soak-ws",
                    "uri": "wss://127.0.0.1:8890",
                    "reachable": True,
                    "lag": 0,
                    "observed_agents": ["peer/a"],
                    "clock_skew_seconds": 0.01,
                }
            ]
        }
    )
    assert health["configured"] is True
    assert health["level"] == GREEN
    assert health["peers_reachable"] == 1
    assert health["peers_unreachable"] == 0
    assert health["peers"][0]["state"] == "reachable"
    assert health["peers"][0]["observed_agents"] == 1


def test_unreachable_peer_is_red() -> None:
    health = build_observed_fleet_health(
        {
            "observed_peers": [
                {
                    "hub_id": "down",
                    "reachable": False,
                    "error": "connection refused",
                }
            ]
        }
    )
    assert health["level"] == RED
    assert health["peers_unreachable"] == 1
    assert health["peers"][0]["state"] == "unreachable"
    assert "connection refused" in health["peers"][0]["detail"]


def test_mixed_reachable_and_lagging() -> None:
    health = build_observed_fleet_health(
        {
            "observed_peers": [
                {"hub_id": "ok", "reachable": True, "lag": 0, "observed_agents": []},
                {"hub_id": "slow", "reachable": True, "lag": 12, "observed_agents": ["x"]},
            ]
        }
    )
    assert health["level"] == AMBER
    assert health["peers_lagging"] == 1
    assert health["peers_reachable"] == 2
    states = {row["hub_id"]: row["state"] for row in health["peers"]}
    assert states["ok"] == "reachable"
    assert states["slow"] == "lagging"


def test_malformed_entries_are_ignored() -> None:
    health = build_observed_fleet_health({"observed_peers": ["not-a-map", 3, None]})
    assert health["configured"] is False
    assert health["peers_total"] == 0


def test_missing_hub_id_defaults_to_unknown() -> None:
    health = build_observed_fleet_health({"observed_peers": [{"reachable": True, "lag": None}]})
    assert health["peers"][0]["hub_id"] == "unknown"

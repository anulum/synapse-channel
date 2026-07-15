# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journeys for the server-facing surface: dashboard and doctor.

Unlike the one-shot query commands, ``dashboard`` binds an HTTP server and
``doctor`` inspects the local machine and a hub. Both are driven against an
isolated hub: the dashboard's ``/snapshot.json`` is fetched and shape-checked (it
is the read-only feed the cockpit and other clients consume), and ``doctor``
is pointed at the isolated hub with ``--uri``.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli_e2e_helpers import http_get, isolated_dashboard, isolated_hub, run_cli


def test_dashboard_serves_the_read_only_fleet_snapshot(tmp_path: Path) -> None:
    """``dashboard`` publishes ``/snapshot.json`` with the fleet snapshot shape."""
    with isolated_hub(tmp_path) as hub:
        run_cli("task", "declare", "BUILD", "--title", "build step", uri=hub.uri)
        with isolated_dashboard(hub.uri) as (base, token):
            status, body = http_get(
                f"{base}/snapshot.json", headers={"Authorization": f"Bearer {token}"}
            )
            assert status == 200, f"status={status} body={body!r}"
            snapshot = json.loads(body)
            # The keys the cockpit and other read-side clients rely on.
            for key in ("online_agents", "state", "board", "manifest", "fleet", "risk"):
                assert key in snapshot, f"missing {key} in {sorted(snapshot)}"


def test_dashboard_index_page_is_served(tmp_path: Path) -> None:
    """``dashboard`` also serves a human index page at the root."""
    with isolated_hub(tmp_path) as hub, isolated_dashboard(hub.uri) as (base, token):
        status, body = http_get(f"{base}/", headers={"Authorization": f"Bearer {token}"})
        assert status == 200, f"status={status}"
        assert "SYNAPSE" in body.upper()


def test_doctor_passes_against_a_live_hub(tmp_path: Path) -> None:
    """``doctor --uri`` reports the isolated hub answered, without failures."""
    with isolated_hub(tmp_path) as hub:
        result = run_cli("doctor", uri=hub.uri)
        # doctor exits 0 with warnings allowed; a failure is a non-zero exit.
        assert result.returncode == 0, result.output
        assert "hub at" in result.output
        assert "no failures" in result.output


def test_doctor_reports_a_failure_when_no_hub_answers() -> None:
    """``doctor`` flags an unreachable hub instead of passing silently."""
    from cli_e2e_helpers import free_port

    dead = f"ws://localhost:{free_port()}"
    result = run_cli("doctor", uri=dead, timeout=20)
    # A missing hub is a real failure signal, not a warning.
    assert "hub" in result.output.lower()
    assert result.returncode != 0


def test_doctor_fix_never_repairs_a_non_default_hub() -> None:
    """``doctor --fix`` refuses to install local services for a non-default hub.

    A dead hub on a random port is a hub the generated user services do not
    manage, so the auto-repair gate must hold: no unit is written, the gate is
    explained, and the manual setup commands are printed instead. This also
    keeps the E2E run itself side-effect free.
    """
    from cli_e2e_helpers import free_port

    dead = f"ws://localhost:{free_port()}"
    result = run_cli("doctor", "--fix", "--project", "demorepo", uri=dead, timeout=20)
    assert result.returncode != 0
    assert "not the default local hub" in result.stdout
    assert "synapse-arm@.service" in result.stdout

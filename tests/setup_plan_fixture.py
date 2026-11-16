# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an inspection and the command line's own plan of it, for setup tests
"""Build an inspection of ``local-single-user`` and plan it with the command line's planner.

The dashboard's setup tests bind their plans to a plan the command line's own
planner made, never to a hand-written one, so a change in the planner reaches
them.
"""

from __future__ import annotations

from synapse_channel.setup_planner import build_setup_plan
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile

TARGET = {"uri": "ws://127.0.0.1:8876", "project": "DEMO", "identity": "DEMO/claude-one"}
"""The inspected target: the default loopback hub, as ``local-single-user`` requires."""


def profile() -> SetupProfile:
    """Return the command line's ``local-single-user`` profile."""
    found = get_setup_profile("local-single-user")
    assert found is not None
    return found


def inspection(
    statuses: dict[str, str] | None = None,
    *,
    hub_pid: int | None = None,
    target: dict[str, str] | None = None,
) -> dict[str, object]:
    """Return one complete inspection; ``statuses`` replaces checks' statuses (all pass)."""
    served = profile()
    status = {item.requirement_id: "pass" for item in served.requirements}
    status.update(statuses or {})
    values: dict[str, object] = {
        "package": {"name": "synapse-channel", "version": "0.99.52"},
        "python": {"executable": "/home/someone/.venv/bin/python", "version": "3.12.0"},
        "platform": {"system": "Linux", "release": "test", "machine": "x86_64"},
        "executable": "/home/someone/.venv/bin/synapse",
        "service_manager": {
            "kind": "systemd-user",
            "executable": "/usr/bin/systemctl",
            "hub_pid": hub_pid or 0,
        },
    }
    checks = [
        {
            "id": item.requirement_id,
            "status": status[item.requirement_id],
            "required": item.required,
            "value": values.get(item.requirement_id, {}),
            "detail": "Observed fixture at ws://127.0.0.1:8876 for DEMO/claude-one.",
            "remedy": "",
        }
        for item in served.requirements
    ]
    return {
        "schema_version": "synapse-setup.v1",
        "document_kind": "inspection",
        "profile": "local-single-user",
        "profile_version": 1,
        "read_only": True,
        "ready": all(
            status[item.requirement_id] == "pass" for item in served.requirements if item.required
        ),
        "target": dict(target or TARGET),
        "summary": {
            name: sum(check["status"] == name for check in checks)
            for name in ("pass", "warn", "fail", "unavailable")
        },
        "checks": checks,
    }


def cli_plan(
    statuses: dict[str, str] | None = None, *, hub_pid: int | None = None
) -> dict[str, object]:
    """Return the command line's own plan of :func:`inspection`."""
    return build_setup_plan(profile(), inspection(statuses, hub_pid=hub_pid))


HUB_AND_WAITER_DOWN = {"hub": "fail", "waiter": "fail"}
"""A host whose hub and waiter are not running: two planned service effects."""

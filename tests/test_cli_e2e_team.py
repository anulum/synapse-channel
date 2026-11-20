# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journey for the ``synapse team`` one-command launcher.

``team`` is the single command that stands up a coordination hub (and, in its
full form, a roster of workers). This drives the launcher as a subprocess in its
``--no-workers`` form — so no model provider is needed; the worker reply path is
covered by the worker journey — and proves the hub it stands up is reachable and
usable: a plain CLI client connects to it, declares a task, and reads it back.
"""

from __future__ import annotations

import time
from pathlib import Path

from cli_e2e_helpers import isolated_team, run_cli


def _board_shows(hub_uri: str, marker: str, *, timeout: float = 8.0) -> bool:
    """Poll the launched hub's board until a line containing ``marker`` appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        board = run_cli("board", uri=hub_uri, timeout=5.0)
        if board.ok() and marker in board.stdout:
            return True
        time.sleep(0.2)
    return False


def test_team_launcher_stands_up_a_usable_hub(tmp_path: Path) -> None:
    with isolated_team() as hub_uri:
        who = run_cli("who", uri=hub_uri)
        assert who.ok(), who.output

        declared = run_cli(
            "task", "declare", "TEAM-1", "--title", "coordinate the fleet", uri=hub_uri
        )
        assert declared.ok(), declared.output
        assert _board_shows(hub_uri, "TEAM-1"), (
            "task declared through the team hub was not served back"
        )

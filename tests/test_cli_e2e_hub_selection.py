# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end hub selection: the ``SYNAPSE_URI`` environment override.

An operator running against a non-default hub — a remote coordinator, a second
local hub on another port — should not have to repeat ``--uri`` on every command.
Setting ``SYNAPSE_URI`` once redirects the whole CLI; an explicit ``--uri`` on a
single command still wins over it. These journeys prove both against a throwaway
hub, driving the packaged CLI by subprocess exactly as a user would.
"""

from __future__ import annotations

from pathlib import Path

from cli_e2e_helpers import free_port, isolated_hub, run_cli


def test_env_uri_routes_a_command_to_the_named_hub(tmp_path: Path) -> None:
    """A task declared with only ``SYNAPSE_URI`` set lands on that exact hub."""
    with isolated_hub(tmp_path) as hub:
        declared = run_cli(
            "task", "declare", "ENVROUTE", "--title", "env routing", env={"SYNAPSE_URI": hub.uri}
        )
        assert declared.ok(), declared.output
        assert "declared ENVROUTE" in declared.stdout

        # Read back over an explicit --uri to the same hub: the env-only write
        # must be visible here, which it can only be if it targeted this hub.
        board = run_cli("board", uri=hub.uri)
        assert board.ok(), board.output
        assert "ENVROUTE" in board.stdout


def test_explicit_uri_overrides_the_environment(tmp_path: Path) -> None:
    """``--uri`` beats a ``SYNAPSE_URI`` pointing at a hub that is not running."""
    dead = f"ws://localhost:{free_port()}"
    with isolated_hub(tmp_path) as hub:
        # Env points at a dead port; the explicit --uri must still reach the hub.
        state = run_cli("state", uri=hub.uri, env={"SYNAPSE_URI": dead})
        assert state.ok(), state.output
        assert "Active claims (0)" in state.stdout


def test_env_uri_pointing_at_a_dead_hub_fails(tmp_path: Path) -> None:
    """With only ``SYNAPSE_URI`` set to a dead port and no ``--uri``, health fails.

    This is the negative counterpart proving the environment is actually consulted
    for hub selection, not silently ignored in favour of the loopback default.
    """
    dead = f"ws://localhost:{free_port()}"
    health = run_cli("health", env={"SYNAPSE_URI": dead}, timeout=15)
    assert health.returncode == 1, health.output

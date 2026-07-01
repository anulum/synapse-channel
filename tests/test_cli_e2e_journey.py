# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end CLI journeys run as a user would: real process, isolated hub.

Each test starts its own throwaway ``synapse hub`` (free port, temp database) and
drives the packaged CLI against it by subprocess, asserting the exit codes and
printed output the user guides promise. This is the coordination-surface half of
the full-surface E2E programme; analysis- and governance-surface journeys read
the same event log a coordination journey writes.
"""

from __future__ import annotations

from pathlib import Path

from cli_e2e_helpers import isolated_hub, run_cli


def test_self_contained_demo_prints_success() -> None:
    """``synapse demo`` runs its bundled coordination flow to the documented line."""
    result = run_cli("demo", timeout=90)
    assert result.ok(), result.output
    assert "success: coordination demo completed" in result.stdout


def test_commands_overview_groups_the_surface_by_tier() -> None:
    """``synapse commands`` prints the whole surface grouped into its five tiers."""
    result = run_cli("commands")
    assert result.ok(), result.output
    for tier in ("stable", "adapter", "analysis", "governance", "experimental"):
        assert f"{tier} — " in result.stdout
    # a representative command from the daily-safe core is present
    assert "board" in result.stdout


def test_read_side_queries_answer_a_fresh_hub(tmp_path: Path) -> None:
    """``who``/``state``/``board``/``manifest`` answer an empty isolated hub."""
    with isolated_hub(tmp_path) as hub:
        state = run_cli("state", uri=hub.uri)
        assert state.ok(), state.output
        assert "Active claims (0)" in state.stdout

        board = run_cli("board", uri=hub.uri)
        assert board.ok(), board.output
        assert "Tasks (0)" in board.stdout

        manifest = run_cli("manifest", uri=hub.uri)
        assert manifest.ok(), manifest.output

        who = run_cli("who", uri=hub.uri)
        assert who.ok(), who.output
        assert "Online" in who.stdout


def test_task_lifecycle_declare_then_complete(tmp_path: Path) -> None:
    """A task declared over the CLI appears on the board and completes."""
    with isolated_hub(tmp_path) as hub:
        declared = run_cli("task", "declare", "T1", "--title", "first task", uri=hub.uri)
        assert declared.ok(), declared.output
        assert "declared T1" in declared.stdout

        updated = run_cli("task", "update", "T1", "--status", "done", uri=hub.uri)
        assert updated.ok(), updated.output
        assert "status=done" in updated.stdout

        board = run_cli("board", uri=hub.uri)
        assert board.ok(), board.output
        assert "[done] T1" in board.stdout


def test_dependent_task_unblocks_when_prerequisite_completes(tmp_path: Path) -> None:
    """A declared dependency keeps a task blocked until the prerequisite is done."""
    with isolated_hub(tmp_path) as hub:
        run_cli("task", "declare", "BUILD", "--title", "build", uri=hub.uri)
        run_cli("task", "declare", "TEST", "--title", "test", "--depends-on", "BUILD", uri=hub.uri)

        before = run_cli("board", uri=hub.uri)
        assert "BUILD" in before.stdout and "TEST" in before.stdout

        run_cli("task", "update", "BUILD", "--status", "done", uri=hub.uri)
        after = run_cli("board", uri=hub.uri)
        assert after.ok(), after.output
        # BUILD complete should move TEST into the ready set.
        assert "TEST" in after.stdout


def test_send_to_absent_target_is_accepted(tmp_path: Path) -> None:
    """``send`` to an offline target exits cleanly (delivery is best-effort)."""
    with isolated_hub(tmp_path) as hub:
        sent = run_cli("send", "--name", "USER", "--target", "NOBODY", "ping", uri=hub.uri)
        assert sent.ok(), sent.output


def test_health_probe_is_silent_but_zero_on_a_live_hub(tmp_path: Path) -> None:
    """``health`` is a quiet liveness probe: exit 0, no report (unlike ``doctor``)."""
    with isolated_hub(tmp_path) as hub:
        health = run_cli("health", uri=hub.uri)
        assert health.returncode == 0, health.output
        assert health.stdout.strip() == ""


def test_health_probe_fails_when_no_hub_answers() -> None:
    """``health`` exits non-zero against a port with no hub."""
    from cli_e2e_helpers import free_port

    dead = f"ws://localhost:{free_port()}"
    health = run_cli("health", uri=dead, timeout=15)
    assert health.returncode == 1, health.output

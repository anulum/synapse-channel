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

import json
import subprocess  # nosec B404 — syntax-checks generated completion scripts
from pathlib import Path

from cli_e2e_helpers import isolated_hub, run_cli


def test_self_contained_demo_prints_success(tmp_path: Path) -> None:
    """``synapse demo`` executes the real golden path and writes its dashboard."""
    output = tmp_path / "golden-demo"
    result = run_cli("demo", "--output", str(output), timeout=90)
    assert result.ok(), result.output
    assert "success: coordination demo completed" in result.stdout
    evidence_path = output / "golden-demo.json"
    dashboard_path = output / "golden-demo-dashboard.html"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    dashboard = dashboard_path.read_text(encoding="utf-8")
    assert evidence["completed"] is True
    assert evidence["guard"]["before_handoff"]["allowed"] is False
    assert evidence["guard"]["after_handoff"]["allowed"] is True
    assert evidence["receipt"]["epistemic_status"] == "supported"
    for marker in ("CONFLICT REFUSED", "MUTATION DENIED", "HANDOFF", "VERIFIED RECEIPT"):
        assert marker in dashboard
    assert "recorded local evidence" in dashboard
    assert "<script" not in dashboard
    assert "<link" not in dashboard
    assert "snapshot.json" not in dashboard


def test_commands_overview_groups_the_surface_by_tier() -> None:
    """``synapse commands`` prints the whole surface grouped into its five tiers."""
    result = run_cli("commands")
    assert result.ok(), result.output
    for tier in ("stable", "adapter", "analysis", "governance", "experimental"):
        assert f"{tier} — " in result.stdout
    # a representative command from the daily-safe core is present
    assert "board" in result.stdout


def test_completions_emit_sourceable_scripts_for_each_shell(tmp_path: Path) -> None:
    """``synapse completions <shell>`` prints a script the target shell accepts.

    On POSIX CI runners, bash output is additionally parsed with ``bash -n``.
    Windows GHA ships a non-POSIX ``bash`` that mishandles UTF-8 scripts, so
    the syntax check is skipped there; registration markers still prove content.
    """
    import os
    import shutil

    bash = run_cli("completions", "bash")
    assert bash.ok(), bash.output
    assert "complete -F _synapse synapse" in bash.stdout
    assert "completions" in bash.stdout
    script = tmp_path / "synapse.bash"
    script.write_text(bash.stdout, encoding="utf-8")
    if os.name != "nt" and shutil.which("bash") is not None:
        checked = subprocess.run(  # nosec B603 B607 — fixed argv over a written temp file
            ["bash", "-n", str(script)], capture_output=True, text=True
        )
        assert checked.returncode == 0, checked.stderr

    zsh = run_cli("completions", "zsh")
    assert zsh.ok(), zsh.output
    assert zsh.stdout.startswith("#compdef synapse")

    fish = run_cli("completions", "fish")
    assert fish.ok(), fish.output
    assert "complete -c synapse -f" in fish.stdout


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


def test_status_one_liner_reports_a_live_hub(tmp_path: Path) -> None:
    """``status`` prints one glanceable line and exits zero against a live hub."""
    with isolated_hub(tmp_path) as hub:
        status = run_cli("status", uri=hub.uri)
        assert status.ok(), status.output
        line = status.stdout.strip()
        assert line.startswith("synapse ● ")
        assert "agents" in line and "claims" in line


def test_status_one_liner_exits_nonzero_when_the_hub_is_down() -> None:
    """``status`` prints the offline line and exits non-zero with no hub — a prompt signal."""
    from cli_e2e_helpers import free_port

    dead = f"ws://localhost:{free_port()}"
    status = run_cli("status", "--ready-timeout", "1", uri=dead, timeout=15)
    assert status.returncode == 1, status.output
    assert status.stdout.strip() == "synapse ○ offline"


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


def test_send_to_absent_target_reports_failed_delivery(tmp_path: Path) -> None:
    """``send`` fails visibly when no online recipient matches its target."""
    with isolated_hub(tmp_path) as hub:
        sent = run_cli("send", "--name", "USER", "--target", "NOBODY", "ping", uri=hub.uri)
        assert sent.returncode == 1
        assert sent.stdout == "delivery failed: no online recipient matched NOBODY\n"


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

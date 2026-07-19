# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journeys for integration edges that read a log or touch a workspace.

These are the seams a user wires by hand: the git claim-release hooks, the shell
startup hook, a downstream consumer draining the event log (``ingest``), and a
read-only fold of a peer hub's log (``multihub observe``). Each runs against an
isolated hub, a temporary git repository, or a sandboxed home directory, so none
of them touches the workstation the suite runs on.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cli_e2e_helpers import git_repo, isolated_hub, run_cli
from hub_e2e_helpers import authorised_multihub_serving_policy, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore


def test_git_hook_install_then_test_reports_resolved_hooks(tmp_path: Path) -> None:
    """``git-hook install`` writes the release hooks and ``test`` confirms them."""
    repo = git_repo(tmp_path / "repo")
    with isolated_hub(tmp_path) as hub:
        installed = run_cli("git-hook", "install", "--name", "tribe", uri=hub.uri, cwd=repo)
        assert installed.ok(), installed.output
        assert "post-commit" in installed.stdout
        assert (repo / ".git" / "hooks" / "post-commit").exists()

        checked = run_cli("git-hook", "test", "--name", "tribe", uri=hub.uri, cwd=repo)
        assert checked.ok(), checked.output
        assert "ok: post-commit installed" in checked.stdout
        assert "ok: post-merge installed" in checked.stdout


def test_install_shell_hook_writes_a_guarded_block_to_the_home_rc(tmp_path: Path) -> None:
    """``install-shell-hook`` appends a guarded startup block to the shell rc file."""
    home = tmp_path / "home"
    home.mkdir()
    result = run_cli("install-shell-hook", "--shell", "bash", env={"HOME": str(home)})
    assert result.ok(), result.output

    rc = home / ".bashrc"
    assert rc.exists()
    body = rc.read_text(encoding="utf-8")
    # The block is delimited so re-running replaces it rather than duplicating.
    assert ">>> synapse-channel shell integration >>>" in body
    assert "command -v -- synapse" in body


def test_ingest_drains_events_and_a_cursor_resumes_without_repeats(tmp_path: Path) -> None:
    """``ingest`` prints the log as JSON lines and a cursor makes a re-run empty."""
    with isolated_hub(tmp_path) as hub:
        run_cli("task", "declare", "INGEST-1", "--title", "drain me", uri=hub.uri)

        cursor = tmp_path / "cursor"
        first = run_cli("ingest", str(hub.db_path), "--cursor", str(cursor))
        assert first.ok(), first.output
        events = [json.loads(line) for line in first.stdout.splitlines() if line.strip()]
        assert any(e["payload"].get("task_id") == "INGEST-1" for e in events)

        # The cursor persisted the last sequence, so a second drain sees nothing new.
        second = run_cli("ingest", str(hub.db_path), "--cursor", str(cursor))
        assert second.ok(), second.output
        assert second.stdout.strip() == ""


def test_multihub_observe_folds_a_peer_logs_board(tmp_path: Path) -> None:
    """``multihub observe`` reads a peer hub's db file and reports its board."""
    with isolated_hub(tmp_path) as hub:
        run_cli("task", "declare", "PEER-1", "--title", "peer task", uri=hub.uri)

        observed = run_cli(
            "multihub", "observe", "--peer-db", str(hub.db_path), "--peer-id", "peerA", "--json"
        )
        assert observed.ok(), observed.output
        payload = json.loads(observed.stdout)
        assert payload["peer_id"] == "peerA"
        assert "PEER-1" in payload["board"]


async def test_multihub_follow_pulls_a_peers_board_over_a_connection(tmp_path: Path) -> None:
    """``multihub follow`` dials a live peer hub and pulls its board snapshot."""
    store = EventStore(tmp_path / "follow-hub.db")
    hub = SynapseHub(
        hub_id="syn-follow",
        journal=store,
        multihub_serving_policy=authorised_multihub_serving_policy("watcher"),
    )
    try:
        async with running_hub(hub) as (_, uri):
            declared = await asyncio.to_thread(
                run_cli, "task", "declare", "FOLLOW-1", "--title", "followed task", uri=uri
            )
            assert declared.ok(), declared.output

            # follow selects the peer with --peer-uri, not the local --uri default.
            followed = await asyncio.to_thread(
                run_cli,
                "multihub",
                "follow",
                "--peer-uri",
                uri,
                "--local-id",
                "watcher",
                "--json",
            )
    finally:
        store.close()

    assert followed.ok(), followed.output
    payload = json.loads(followed.stdout)
    assert "FOLLOW-1" in payload["board"]

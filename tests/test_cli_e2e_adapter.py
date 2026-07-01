# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journeys for the adapter surface: interop and integration edges.

These commands sit at the seams between Synapse and the tools around it — editor
adapters, the shell hook, the A2A Agent Card, and the git claim hooks. The
externally-heavy adapters (``mcp*`` needing a live MCP server, ``worker`` needing
a model provider, ``agent-tmux``/``codex-tmux`` needing tmux) are driven by their
own journeys; this module covers the ones that run against a repo and an isolated
hub.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli_e2e_helpers import git_repo, git_run, isolated_hub, run_cli

# --- editor adapters and shell integration ----------------------------------


def test_adapters_list_shows_detected_tools() -> None:
    """``adapters list`` reports each known tool and where its guide would live."""
    result = run_cli("adapters", "list")
    assert result.ok(), result.output
    assert "claude-code" in result.stdout
    assert "codex" in result.stdout


def test_shell_hook_emits_a_script_per_shell() -> None:
    """``shell-hook`` prints an integration script for each supported shell."""
    for shell in ("bash", "fish", "zsh"):
        result = run_cli("shell-hook", "--shell", shell)
        assert result.ok(), result.output
        assert "SYNAPSE_AUTO_CONNECT" in result.stdout


# --- A2A Agent Card ----------------------------------------------------------


def test_a2a_card_emits_valid_agent_card_json(tmp_path: Path) -> None:
    """``a2a-card`` prints a JSON Agent Card; it reads capabilities from the hub."""
    with isolated_hub(tmp_path) as hub:
        result = run_cli(
            "a2a-card", "--endpoint-url", "http://127.0.0.1:8877", "--name", "TRIAL", uri=hub.uri
        )
        assert result.ok(), result.output
        card = json.loads(result.stdout)
        assert "capabilities" in card


# --- git claim hooks ---------------------------------------------------------


def test_git_init_installs_hooks_and_conventions(tmp_path: Path) -> None:
    """``git-init`` installs release hooks and writes the claim conventions guide."""
    repo = git_repo(tmp_path / "repo")
    with isolated_hub(tmp_path) as hub:
        result = run_cli("git-init", "--name", "trial-agent", uri=hub.uri, cwd=repo)
        assert result.ok(), result.output
        assert (repo / ".synapse" / "git-claims.md").exists()
        assert (repo / ".git" / "hooks" / "post-commit").exists()
        assert (repo / ".git" / "hooks" / "post-merge").exists()


def test_git_claim_then_release_over_a_branch(tmp_path: Path) -> None:
    """A branch claim registers on the hub and the matching release clears it."""
    repo = git_repo(tmp_path / "repo")
    git_run(repo, "checkout", "-q", "-b", "feature/x")
    with isolated_hub(tmp_path) as hub:
        run_cli("git-init", "--name", "trial-agent", uri=hub.uri, cwd=repo)
        claimed = run_cli(
            "git-claim", "--task-id", "edit-x", "--paths", "src/x.py", uri=hub.uri, cwd=repo
        )
        assert claimed.ok(), claimed.output

        state = run_cli("state", uri=hub.uri)
        assert "edit-x" in state.stdout

        released = run_cli("git-release", "--trigger", "commit", uri=hub.uri, cwd=repo)
        assert released.ok(), released.output

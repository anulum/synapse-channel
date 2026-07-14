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

from cli_e2e_helpers import (
    A2A_AGENT_CARD_PATH,
    git_repo,
    git_run,
    http_get,
    isolated_a2a_serve,
    isolated_hub,
    run_cli,
)

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


def test_a2a_serve_publishes_an_agent_card_over_http(tmp_path: Path) -> None:
    """``a2a-serve`` binds an HTTP bridge that serves the Agent Card at its well-known path."""
    with isolated_hub(tmp_path) as hub, isolated_a2a_serve(hub.uri) as base:
        status, body = http_get(f"{base}{A2A_AGENT_CARD_PATH}")
        assert status == 200, body
        card = json.loads(body)
        assert card["name"]
        assert "capabilities" in card
        assert isinstance(card["skills"], list)


def test_a2a_serve_enforces_real_origin_and_host_boundaries(tmp_path: Path) -> None:
    """The real CLI bind admits only its exact Origin and advertised Host boundary."""
    with (
        isolated_hub(tmp_path) as hub,
        isolated_a2a_serve(
            hub.uri,
            allowed_origins=("https://ide.example",),
        ) as base,
    ):
        card_url = f"{base}{A2A_AGENT_CARD_PATH}"
        authority = base.removeprefix("http://")

        allowed, body = http_get(
            card_url,
            headers={"Host": authority, "Origin": "https://IDE.example/"},
        )
        assert allowed == 200, body

        denied_origin, body = http_get(
            card_url,
            headers={"Host": authority, "Origin": "https://evil.example"},
        )
        assert denied_origin == 403
        assert json.loads(body)["detail"] == "Origin or Host not allowed"

        no_origin, body = http_get(card_url, headers={"Host": authority})
        assert no_origin == 200, body

        hostile_host, body = http_get(card_url, headers={"Host": "attacker.example"})
        assert hostile_host == 403
        assert json.loads(body)["detail"] == "Origin or Host not allowed"

        opaque_origin, _body = http_get(
            card_url,
            headers={"Host": authority, "Origin": "null"},
        )
        assert opaque_origin == 403


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

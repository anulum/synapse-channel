# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real CLI/hub journey for the Claude Edit/Write claim guard

from __future__ import annotations

import json
import sys
from pathlib import Path

from cli_e2e_helpers import git_repo, isolated_hub, run_cli


def _event(repo: Path, target: Path) -> str:
    return json.dumps(
        {
            "session_id": "e2e-session",
            "tool_use_id": f"tool-{target.name}",
            "cwd": str(repo),
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "content\n"},
        }
    )


def test_live_claim_allows_owned_file_and_denies_unclaimed_file(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    (repo / "src").mkdir()
    with isolated_hub(tmp_path) as hub:
        claimed = run_cli(
            "git-claim",
            "CLAUDE-E2E",
            "--paths",
            "src/owned.py",
            "--auto-release-on",
            "manual",
            "--name",
            "seat/one",
            uri=hub.uri,
            cwd=repo,
        )
        assert claimed.ok(), claimed.output

        allowed = run_cli(
            "adapters",
            "claude-claim-hook",
            "--identity",
            "seat/one",
            stdin=_event(repo, repo / "src" / "owned.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert allowed.ok(), allowed.output
        assert allowed.stdout == ""

        denied = run_cli(
            "adapters",
            "claude-claim-hook",
            "--identity",
            "seat/one",
            stdin=_event(repo, repo / "src" / "other.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert denied.ok(), denied.output
        output = json.loads(denied.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_cli_prints_mergeable_config_without_writing_settings(tmp_path: Path) -> None:
    result = run_cli(
        "adapters",
        "claude-claim-hook",
        "--identity",
        "seat/one",
        "--print-config",
        "--synapse-bin",
        sys.executable,
        cwd=tmp_path,
    )
    assert result.ok(), result.output
    config = json.loads(result.stdout)
    assert config["hooks"]["PreToolUse"][0]["matcher"] == "Edit|Write"
    assert not (tmp_path / ".claude").exists()

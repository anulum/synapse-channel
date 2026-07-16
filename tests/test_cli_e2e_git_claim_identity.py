# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — packaged canonical claim identity journey
"""Prove hard-link exclusion and fail-closed staged coverage through the CLI."""

from __future__ import annotations

import os
from pathlib import Path

from cli_e2e_helpers import git_repo, git_run, isolated_hub, run_cli

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CLEAN_ENV = {
    "PYTHONPATH": str(_PROJECT_ROOT / "src"),
    "SYN_PROJECT": "",
    "SYN_IDENTITY": "",
    "SYNAPSE_URI": "",
    "SYNAPSE_TOKEN": "",
}


def test_hardlink_alias_is_one_claim_scope_end_to_end(tmp_path: Path) -> None:
    """Deny a second alias claim without treating inode history as authorization."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "owned.py"
    alias = repo / "alias.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    os.link(source, alias)
    git_run(repo, "add", "owned.py", "alias.py")
    git_run(repo, "commit", "-q", "-m", "hardlink fixture")

    with isolated_hub(tmp_path) as hub:
        configured = run_cli(
            "git-init",
            "--name",
            "project/owner",
            uri=hub.uri,
            cwd=repo,
            env=_CLEAN_ENV,
        )
        assert configured.ok(), configured.output

        claimed = run_cli(
            "git-claim",
            "--task-id",
            "CANONICAL-OWNER",
            "--paths",
            "owned.py",
            "--auto-release-on",
            "manual",
            "--name",
            "project/owner",
            uri=hub.uri,
            cwd=repo,
            env=_CLEAN_ENV,
        )
        assert claimed.ok(), claimed.output

        competing = run_cli(
            "git-claim",
            "--task-id",
            "ALIAS-COMPETITOR",
            "--paths",
            "alias.py",
            "--auto-release-on",
            "manual",
            "--name",
            "project/other",
            uri=hub.uri,
            cwd=repo,
            env=_CLEAN_ENV,
        )
        assert competing.returncode == 1
        assert "file scope conflicts" in competing.stdout

        alias.write_text("VALUE = 2\n", encoding="utf-8")
        git_run(repo, "add", "alias.py")
        guarded = run_cli("git-claim-check", "--staged", cwd=repo, env=_CLEAN_ENV)
        assert guarded.returncode == 1
        assert 'no covering claim: "alias.py"' in guarded.stderr

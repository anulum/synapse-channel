# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — packaged staged claim-check journeys

from __future__ import annotations

from pathlib import Path

from cli_e2e_helpers import git_repo, git_run, isolated_hub, run_cli

_CLEAN_IDENTITY_ENV = {
    "SYN_PROJECT": "",
    "SYN_IDENTITY": "",
    "SYNAPSE_URI": "",
    "SYNAPSE_TOKEN": "",
}


def _stage_source(repo: Path) -> None:
    source = repo / "src" / "new.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    git_run(repo, "add", "src/new.py")


def test_empty_index_succeeds_without_identity_or_reachable_hub(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    result = run_cli(
        "git-claim-check",
        "--staged",
        "--uri",
        "ws://127.0.0.1:1",
        cwd=repo,
        env=_CLEAN_IDENTITY_ENV,
    )
    assert result.ok(), result.output
    assert "no staged paths" in result.stdout


def test_live_hub_denies_uncovered_then_allows_exact_owned_claim(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    identity = "project/agent"
    with isolated_hub(tmp_path) as hub:
        initialised = run_cli(
            "git-init",
            "--name",
            identity,
            uri=hub.uri,
            cwd=repo,
            env=_CLEAN_IDENTITY_ENV,
        )
        assert initialised.ok(), initialised.output
        _stage_source(repo)

        uncovered = run_cli("git-claim-check", "--staged", cwd=repo, env=_CLEAN_IDENTITY_ENV)
        assert uncovered.returncode == 1
        assert "no covering claim" in uncovered.stderr
        assert '"src/new.py"' in uncovered.stderr

        claimed = run_cli(
            "git-claim",
            "--task-id",
            "SOURCE-EDIT",
            "--paths",
            "src/new.py",
            "--auto-release-on",
            "manual",
            "--name",
            identity,
            uri=hub.uri,
            cwd=repo,
            env=_CLEAN_IDENTITY_ENV,
        )
        assert claimed.ok(), claimed.output
        allowed = run_cli("git-claim-check", "--staged", cwd=repo, env=_CLEAN_IDENTITY_ENV)
        assert allowed.ok(), allowed.output
        assert "OK (1 paths)" in allowed.stdout

    unavailable = run_cli(
        "git-claim-check",
        "--staged",
        "--timeout",
        "0.2",
        cwd=repo,
        env=_CLEAN_IDENTITY_ENV,
    )
    assert unavailable.returncode == 1
    assert "unavailable" in unavailable.stderr.lower()

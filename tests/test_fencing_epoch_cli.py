# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — FENCE-01b: the lease epoch travels from the claiming to the releasing process
"""The CLI claims in one process and releases in another, under strict fencing.

``git-claim`` and the later ``release`` or commit-hook ``git-release`` are
separate processes, so the epoch the claim was granted must travel between
them. Each process here is the real ``synapse`` command against a real hub
started with ``--require-fencing-epoch``. With the claimer's data home the
release names the stored epoch and is granted; once the epoch store is gone the
same release names no epoch and the hub refuses it, which is the gap the store
closes. A default hub still grants the epoch-less release. The negative controls keep
the data home, and with it the machine identity the hub pinned, and drop only
the epoch store.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from _platform_caps import POSIX_MODE_BITS_MEANINGFUL
from cli_e2e_helpers import git_repo, git_run, isolated_hub, run_cli
from synapse_channel.core.handlers.leasing import FENCING_EPOCH_REQUIRED

STRICT = ("--require-fencing-epoch",)


def _claim(tmp_path: Path, hub_uri: str, data_home: Path, *extra: str) -> Path:
    """Claim ``edit-z`` over a feature branch from its own process."""
    repo = git_repo(tmp_path / "repo")
    git_run(repo, "checkout", "-q", "-b", "feature/z")
    env = {"XDG_DATA_HOME": str(data_home)}
    assert run_cli("git-init", "--name", "trial", uri=hub_uri, cwd=repo, env=env).ok()
    claimed = run_cli(
        "git-claim",
        "--task-id",
        "edit-z",
        "--paths",
        "src/z.py",
        *extra,
        uri=hub_uri,
        cwd=repo,
        env=env,
    )
    assert claimed.ok(), claimed.output
    return repo


def _state(hub_uri: str, data_home: Path) -> str:
    """Read the hub state as the claimer's machine identity, failing loudly otherwise."""
    state = run_cli("state", uri=hub_uri, env={"XDG_DATA_HOME": str(data_home)})
    assert state.ok(), state.output
    return state.stdout


def _stored_epochs(data_home: Path) -> list[Path]:
    root = data_home / "synapse" / "lease-epoch"
    return sorted(path for path in root.rglob("*") if path.is_file()) if root.exists() else []


def test_a_release_from_another_process_names_the_stored_epoch(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    with isolated_hub(tmp_path, extra_args=STRICT) as hub:
        _claim(tmp_path, hub.uri, data_home)
        [stored] = _stored_epochs(data_home)
        assert stored.name == "edit-z"
        assert stored.read_text(encoding="ascii").isdigit()
        if POSIX_MODE_BITS_MEANINGFUL:
            assert stored.stat().st_mode & 0o777 == 0o600
        released = run_cli(
            "release",
            "edit-z",
            "--name",
            "USER",
            uri=hub.uri,
            env={"XDG_DATA_HOME": str(data_home)},
        )
        assert released.ok(), released.output
        assert "edit-z" not in _state(hub.uri, data_home)
    assert _stored_epochs(data_home) == []  # the granted release forgot it


def test_the_commit_hook_release_names_the_stored_epoch(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    with isolated_hub(tmp_path, extra_args=STRICT) as hub:
        repo = _claim(tmp_path, hub.uri, data_home, "--auto-release-on", "commit")
        # Commit the claimed path with the installed hooks off, then run the hook's own
        # release command as the claimer, from its own process.
        (repo / "src").mkdir(exist_ok=True)
        (repo / "src" / "z.py").write_text("VALUE = 1\n", encoding="utf-8")
        git_run(repo, "add", "src/z.py")
        no_hooks = tmp_path / "no-hooks"
        no_hooks.mkdir()
        git_run(repo, "-c", f"core.hooksPath={no_hooks}", "commit", "-q", "-m", "edit z")
        released = run_cli(
            "git-release",
            "--trigger",
            "commit",
            uri=hub.uri,
            cwd=repo,
            env={"XDG_DATA_HOME": str(data_home)},
        )
        assert released.ok(), released.output
        assert "edit-z" not in _state(hub.uri, data_home)


def _forget_stored_epochs(data_home: Path) -> dict[str, str]:
    """Drop the epoch store but keep the data home, so the machine identity stays."""
    shutil.rmtree(data_home / "synapse" / "lease-epoch")
    return {"XDG_DATA_HOME": str(data_home)}


def test_without_the_stored_epoch_strict_fencing_refuses_the_release(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    with isolated_hub(tmp_path, extra_args=STRICT) as hub:
        _claim(tmp_path, hub.uri, data_home)
        refused = run_cli(
            "release", "edit-z", "--name", "USER", uri=hub.uri, env=_forget_stored_epochs(data_home)
        )
        assert refused.returncode == 1
        assert FENCING_EPOCH_REQUIRED in refused.output
        assert "edit-z" in _state(hub.uri, data_home)


def test_a_default_hub_still_grants_the_epochless_release(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    with isolated_hub(tmp_path) as hub:
        _claim(tmp_path, hub.uri, data_home)
        released = run_cli(
            "release", "edit-z", "--name", "USER", uri=hub.uri, env=_forget_stored_epochs(data_home)
        )
        assert released.ok(), released.output

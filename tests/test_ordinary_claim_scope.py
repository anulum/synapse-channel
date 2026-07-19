# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ordinary path-claim Git identity tests
"""Exercise optional Git binding for ordinary first-party file claims."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli_e2e_helpers import git_repo
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.ordinary_claim import (
    OrdinaryClaimScopeError,
    _has_git_marker,
    resolve_ordinary_claim_scope,
)


def test_ordinary_scope_binds_paths_inside_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file claim inside Git carries the canonical worktree and identity."""
    repo = git_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    scope = resolve_ordinary_claim_scope(["src/new.py"])

    assert scope is not None
    assert scope.worktree == repo.resolve().as_posix()
    assert scope.paths == ("src/new.py",)
    assert scope.path_identity["worktree_path"] == repo.resolve().as_posix()
    paths = scope.path_identity["paths"]
    assert isinstance(paths, list)
    assert paths[0]["filesystem_path"] == "src/new.py"


def test_ordinary_scope_preserves_non_git_and_refuses_git_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside Git keeps legacy scope; an unreadable checkout fails closed."""
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert resolve_ordinary_claim_scope(["src/new.py"]) is None

    repo = git_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    with pytest.raises(OrdinaryClaimScopeError, match="canonical path identity"):
        resolve_ordinary_claim_scope(["../outside"])

    def broken_git(_args: list[str]) -> str:
        raise GitError("fixture Git failure")

    with pytest.raises(OrdinaryClaimScopeError, match="could not resolve"):
        resolve_ordinary_claim_scope(["src/new.py"], runner=broken_git)


def test_empty_ordinary_scope_never_probes_git() -> None:
    """A keyless named mutex remains independent of the current checkout."""
    called = False

    def forbidden_git(_args: list[str]) -> str:
        nonlocal called
        called = True
        raise AssertionError("empty scope must not invoke Git")

    assert resolve_ordinary_claim_scope([], runner=forbidden_git) is None
    assert called is False


def test_git_marker_probe_fails_closed_on_unresolvable_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uncertain checkout state must not enable the legacy namespace."""

    def broken_resolve(self: Path, *, strict: bool = False) -> Path:
        del self, strict
        raise OSError("fixture path failure")

    monkeypatch.setattr(Path, "resolve", broken_resolve)

    assert _has_git_marker(Path("unreadable")) is True

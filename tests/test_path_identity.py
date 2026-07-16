# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — filesystem-canonical claim identity regressions
"""Exercise canonical claim identity against real Git and filesystem aliases."""

from __future__ import annotations

import ctypes
import os
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import git_repo, git_run
from synapse_channel.core.handlers.leasing import apply_claim
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.path_identity import (
    CanonicalPathIdentity,
    ClaimScopeIdentity,
    PathIdentityError,
    claim_scopes_conflict,
)
from synapse_channel.core.state import SynapseState
from synapse_channel.git.path_identity import (
    detect_case_sensitivity,
    resolve_claim_scope_identity,
)


def _commit(repo: Path, *relative_paths: str) -> None:
    """Add and commit selected fixture paths in one real Git index."""
    git_run(repo, "add", "--", *relative_paths)
    git_run(repo, "commit", "-q", "-m", "identity fixtures")


def _single(scope: ClaimScopeIdentity, index: int) -> ClaimScopeIdentity:
    """Return a one-path view of a resolved scope identity."""
    return ClaimScopeIdentity(
        worktree_path=scope.worktree_path,
        worktree_object_id=scope.worktree_object_id,
        filesystem_namespace=scope.filesystem_namespace,
        case_sensitive=scope.case_sensitive,
        paths=(scope.paths[index],),
    )


def _identity(
    *,
    worktree: str = "/repo",
    path: str = "src/file.py",
    case_sensitive: bool = True,
    filesystem_path: str | None = None,
    object_id: str = "",
) -> ClaimScopeIdentity:
    """Build one valid comparison identity for pure conflict tests."""
    canonical = path if case_sensitive else path.casefold()
    filesystem = filesystem_path if filesystem_path is not None else canonical
    if not case_sensitive:
        filesystem = filesystem.casefold()
    return ClaimScopeIdentity(
        worktree_path=worktree if case_sensitive else worktree.casefold(),
        worktree_object_id="root:1",
        filesystem_namespace="host:1",
        case_sensitive=case_sensitive,
        paths=(CanonicalPathIdentity(canonical, filesystem, object_id),),
    )


def test_identity_wire_round_trip_and_validation() -> None:
    scope = _identity(object_id="1a:2b")
    assert ClaimScopeIdentity.from_dict(scope.as_dict()) == scope

    malformed = scope.as_dict()
    malformed["case_sensitive"] = "yes"
    with pytest.raises(PathIdentityError, match="boolean"):
        ClaimScopeIdentity.from_dict(malformed)

    malformed = scope.as_dict()
    malformed["paths"] = [{"git_path": "../outside", "filesystem_path": "x"}]
    with pytest.raises(PathIdentityError, match="canonical"):
        ClaimScopeIdentity.from_dict(malformed)


def test_hub_refuses_malformed_additive_identity_without_mutation() -> None:
    hub = SynapseHub()
    application = apply_claim(
        hub,
        "seat/one",
        {
            "task_id": "BAD-IDENTITY",
            "worktree": "/repo",
            "paths": ["src"],
            "path_identity": {"version": 999},
        },
    )
    assert not application.ok
    assert application.claim is None
    assert hub.state.claims == {}

    scope = _identity()
    application = apply_claim(
        hub,
        "seat/one",
        {
            "task_id": "MISALIGNED-IDENTITY",
            "worktree": "/repo",
            "paths": ["src/other.py"],
            "path_identity": scope.as_dict(),
        },
    )
    assert not application.ok
    assert hub.state.claims == {}
    assert hub.state.last_seen == {}

    forged_root = _identity(worktree="/forged")
    application = apply_claim(
        hub,
        "seat/one",
        {
            "task_id": "FORGED-WORKTREE-IDENTITY",
            "worktree": "/repo",
            "paths": ["src/file.py"],
            "path_identity": forged_root.as_dict(),
        },
    )
    assert not application.ok
    assert hub.state.claims == {}


def test_hub_accepts_typescript_compatible_bound_identity_envelope() -> None:
    """The JS shape carries both ordinary worktree and additive identity fields."""
    hub = SynapseHub()
    identity = _identity()
    application = apply_claim(
        hub,
        "seat/js",
        {
            "task_id": "JS-IDENTITY",
            "worktree": identity.worktree_path,
            "paths": ["src/file.py"],
            "path_identity": identity.as_dict(),
        },
    )
    assert application.ok
    assert hub.state.claims["JS-IDENTITY"].path_identity == identity


def test_mixed_version_case_and_unicode_fallback_is_conservative() -> None:
    insensitive = _identity(path="src/foo.py", case_sensitive=False)
    assert claim_scopes_conflict(
        "/repo",
        ["src/Foo.py"],
        None,
        "/REPO",
        ["src/foo.py"],
        insensitive,
    )

    sensitive = _identity(path="src/Foo.py", case_sensitive=True)
    assert not claim_scopes_conflict(
        "/repo",
        ["src/foo.py"],
        None,
        "/repo",
        ["src/Foo.py"],
        sensitive,
    )

    decomposed = "notes/cafe\u0301.md"
    composed = unicodedata.normalize("NFC", decomposed)
    unicode_scope = _identity(path=composed)
    assert claim_scopes_conflict(
        "/repo",
        [decomposed],
        None,
        "/repo",
        [composed],
        unicode_scope,
    )


def test_real_git_identity_detects_symlink_and_hardlink_aliases(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    real = repo / "real.py"
    hard = repo / "hard.py"
    alias = repo / "alias.py"
    real.write_text("VALUE = 1\n", encoding="utf-8")
    os.link(real, hard)
    symlink_supported = True
    try:
        alias.symlink_to(real.name)
    except OSError:
        symlink_supported = False
    committed = ["real.py", "hard.py"]
    if symlink_supported:
        committed.append("alias.py")
    _commit(repo, *committed)

    root, displays, scope = resolve_claim_scope_identity(repo, committed)

    assert root == repo.resolve()
    assert displays == tuple(committed)
    assert scope.paths[0].object_id == scope.paths[1].object_id
    assert claim_scopes_conflict(
        str(root),
        [displays[0]],
        _single(scope, 0),
        str(root),
        [displays[1]],
        _single(scope, 1),
    )
    if symlink_supported:
        assert scope.paths[0].filesystem_path == scope.paths[2].filesystem_path
        assert claim_scopes_conflict(
            str(root),
            [displays[0]],
            _single(scope, 0),
            str(root),
            [displays[2]],
            _single(scope, 2),
        )

    state = SynapseState()
    first_ok, _ = state.claim(
        "seat/one",
        "REAL",
        worktree=str(root),
        paths=[displays[0]],
        path_identity=_single(scope, 0),
    )
    second_ok, reason = state.claim(
        "seat/two",
        "HARD",
        worktree=str(root),
        paths=[displays[1]],
        path_identity=_single(scope, 1),
    )
    assert first_ok
    assert not second_ok
    assert "file scope conflicts" in reason


def test_symlink_escape_is_refused_before_claim(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    alias = repo / "outside-link.py"
    try:
        alias.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    _commit(repo, "outside-link.py")

    with pytest.raises(PathIdentityError, match="outside"):
        resolve_claim_scope_identity(repo, ["outside-link.py"])


def test_symlinked_worktree_label_resolves_to_one_canonical_root(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    alias = tmp_path / "repo-alias"
    try:
        alias.symlink_to(repo, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this host")

    direct_root, _, direct = resolve_claim_scope_identity(repo, ["README.md"])
    alias_root, _, through_alias = resolve_claim_scope_identity(alias, ["README.md"])

    assert alias_root == direct_root
    assert through_alias.worktree_path == direct.worktree_path


def test_unicode_nfc_preserves_display_but_stabilises_identity(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    decomposed = "cafe\u0301.py"
    (repo / decomposed).write_text("VALUE = 1\n", encoding="utf-8")
    _commit(repo, decomposed)

    _, displays, scope = resolve_claim_scope_identity(repo, [decomposed])

    assert displays == (decomposed,)
    assert scope.paths[0].git_path == unicodedata.normalize("NFC", decomposed)
    assert scope.validates_display_paths(displays)


def test_real_filesystem_case_policy_never_collapses_linux_distinct_files(
    tmp_path: Path,
) -> None:
    repo = git_repo(tmp_path / "repo")
    source = repo / "Src"
    source.mkdir()
    (source / "Foo.py").write_text("UPPER = 1\n", encoding="utf-8")
    _commit(repo, "Src/Foo.py")
    sensitive = detect_case_sensitivity(repo)

    if sensitive:
        (source / "foo.py").write_text("LOWER = 1\n", encoding="utf-8")
        _commit(repo, "Src/foo.py")
        root, displays, scope = resolve_claim_scope_identity(
            repo,
            ["Src/Foo.py", "Src/foo.py"],
        )
        assert not claim_scopes_conflict(
            str(root),
            [displays[0]],
            _single(scope, 0),
            str(root),
            [displays[1]],
            _single(scope, 1),
        )
        return

    root, displays, scope = resolve_claim_scope_identity(repo, ["src/foo.py", "Src/Foo.py"])
    assert claim_scopes_conflict(
        str(root),
        [displays[0]],
        _single(scope, 0),
        str(root),
        [displays[1]],
        _single(scope, 1),
    )


def test_case_probe_treats_distinct_hardlink_spellings_as_sensitive(tmp_path: Path) -> None:
    """Two exact directory entries prove case sensitivity even with one inode."""
    lower = tmp_path / "a"
    upper = tmp_path / "A"
    lower.write_text("x", encoding="utf-8")
    try:
        os.link(lower, upper)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")
    assert detect_case_sensitivity(tmp_path, runner=lambda _args: "false")


@pytest.mark.skipif(os.name != "nt", reason="Windows 8.3 aliases are Windows-only")
def test_windows_short_name_resolves_to_long_filesystem_identity(tmp_path: Path) -> None:
    """Prove the OS canonicaliser expands an available 8.3 alias."""
    repo = git_repo(tmp_path / "repo")
    long_name = "LongCanonicalFilenameForClaim.py"
    target = repo / long_name
    target.write_text("VALUE = 1\n", encoding="utf-8")
    _commit(repo, long_name)
    buffer = ctypes.create_unicode_buffer(32768)
    ctypes_runtime: Any = ctypes
    length = ctypes_runtime.windll.kernel32.GetShortPathNameW(str(target), buffer, len(buffer))
    if length == 0 or Path(buffer.value).name.casefold() == long_name.casefold():
        pytest.skip("8.3 short-name generation is disabled on this volume")
    short_name = Path(buffer.value).name

    root, displays, scope = resolve_claim_scope_identity(repo, [short_name, long_name])

    assert scope.paths[0].filesystem_path == scope.paths[1].filesystem_path
    assert claim_scopes_conflict(
        str(root),
        [displays[0]],
        _single(scope, 0),
        str(root),
        [displays[1]],
        _single(scope, 1),
    )

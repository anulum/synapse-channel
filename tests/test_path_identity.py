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
from synapse_channel.core.scoping import MAX_PATH_LENGTH
from synapse_channel.core.state import SynapseState
from synapse_channel.git.path_identity import (
    detect_case_sensitivity,
    resolve_claim_scope_identity,
)
from synapse_channel.git.semantic_scope import parse_semantic_scope, semantic_scope_path


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
    object_scope: str = "",
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
        paths=(
            CanonicalPathIdentity(
                canonical,
                filesystem,
                object_id,
                object_scope,
            ),
        ),
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


def test_semantic_identity_resolves_source_without_colliding_siblings(
    tmp_path: Path,
) -> None:
    """Synthetic siblings share source identity without sharing declaration scope."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "src" / "worker.py"
    source.parent.mkdir()
    source.write_text(
        "def first():\n    return 1\n\n\ndef second():\n    return 2\n",
        encoding="utf-8",
    )
    _commit(repo, "src/worker.py")
    first = semantic_scope_path("src/worker.py", "first")
    second = semantic_scope_path("src/worker.py", "second")

    root, displays, scope = resolve_claim_scope_identity(repo, [first, second])

    assert displays == (first, second)
    assert [identity.filesystem_path for identity in scope.paths] == [first, second]
    assert scope.paths[0].object_id == scope.paths[1].object_id != ""
    assert [identity.object_scope for identity in scope.paths] == ["first", "second"]
    assert not claim_scopes_conflict(
        str(root),
        [first],
        _single(scope, 0),
        str(root),
        [second],
        _single(scope, 1),
    )


def test_semantic_identity_preserves_hardlink_alias_hierarchy(tmp_path: Path) -> None:
    """Hard-link aliases retain semantic ancestry without merging siblings."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "src" / "worker.py"
    alias = repo / "src" / "worker_alias.py"
    source.parent.mkdir()
    source.write_text("class Worker:\n    def run(self):\n        return 1\n", encoding="utf-8")
    os.link(source, alias)
    _commit(repo, "src/worker.py", "src/worker_alias.py")
    parent = semantic_scope_path("src/worker.py", "Worker")
    child = semantic_scope_path("src/worker_alias.py", "Worker.run")
    sibling = semantic_scope_path("src/worker.py", "Worker.stop")

    root, displays, scope = resolve_claim_scope_identity(repo, [parent, child, sibling])

    filesystem_scopes = [parse_semantic_scope(identity.filesystem_path) for identity in scope.paths]
    assert displays == (parent, child, sibling)
    assert all(item is not None for item in filesystem_scopes)
    assert {item.source for item in filesystem_scopes if item is not None} == {
        "src/worker.py",
        "src/worker_alias.py",
    }
    assert len({identity.object_id for identity in scope.paths}) == 1
    assert [identity.object_scope for identity in scope.paths] == [
        "Worker",
        "Worker/run",
        "Worker/stop",
    ]
    assert claim_scopes_conflict(
        str(root),
        [parent],
        _single(scope, 0),
        str(root),
        [child],
        _single(scope, 1),
    )
    assert not claim_scopes_conflict(
        str(root),
        [sibling],
        _single(scope, 2),
        str(root),
        [child],
        _single(scope, 1),
    )


def test_semantic_object_scope_honours_shared_case_policy() -> None:
    """Object-relative symbols cannot bypass a case-insensitive worktree."""
    upper = _identity(
        path=semantic_scope_path("source.py", "Worker.Run"),
        case_sensitive=True,
        object_id="1:2",
        object_scope="Worker/Run",
    )
    lower = _identity(
        path=semantic_scope_path("source_alias.py", "worker.run"),
        case_sensitive=False,
        object_id="1:2",
        object_scope="worker/run",
    )

    assert claim_scopes_conflict(
        "/repo",
        ["source.py/.synapse-symbol/Worker/Run"],
        upper,
        "/repo",
        ["source_alias.py/.synapse-symbol/worker/run"],
        lower,
    )


def test_whole_file_conflicts_with_semantic_scope_through_hardlink_alias(
    tmp_path: Path,
) -> None:
    """A whole source object covers every semantic descendant through aliases."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "a.py"
    alias = repo / "b.py"
    source.write_text("class Worker:\n    def run(self):\n        return 1\n", encoding="utf-8")
    os.link(source, alias)
    _commit(repo, "a.py", "b.py")
    semantic = semantic_scope_path("b.py", "Worker.run")

    root, displays, scope = resolve_claim_scope_identity(repo, ["a.py", semantic])

    assert scope.paths[0].object_id == scope.paths[1].object_id != ""
    assert scope.paths[0].object_scope == ""
    assert scope.paths[1].object_scope == "Worker/run"
    assert claim_scopes_conflict(
        str(root),
        [displays[0]],
        _single(scope, 0),
        str(root),
        [displays[1]],
        _single(scope, 1),
    )
    state = SynapseState()
    first_ok, _ = state.claim(
        "seat/whole",
        "WHOLE",
        worktree=str(root),
        paths=[displays[0]],
        path_identity=_single(scope, 0),
    )
    second_ok, reason = state.claim(
        "seat/semantic",
        "SEMANTIC",
        worktree=str(root),
        paths=[displays[1]],
        path_identity=_single(scope, 1),
    )
    assert first_ok
    assert not second_ok
    assert "file scope conflicts" in reason


def test_semantic_object_scope_survives_hardlink_creation(tmp_path: Path) -> None:
    """Later hard-link creation cannot disconnect an existing semantic claim."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "a.py"
    alias = repo / "b.py"
    source.write_text("class Worker:\n    def run(self):\n        return 1\n", encoding="utf-8")
    _commit(repo, "a.py")
    original = semantic_scope_path("a.py", "Worker.run")
    root, original_displays, original_scope = resolve_claim_scope_identity(
        repo,
        [original],
    )

    os.link(source, alias)
    _commit(repo, "b.py")
    aliased = semantic_scope_path("b.py", "Worker.run")
    _root, aliased_displays, aliased_scope = resolve_claim_scope_identity(
        repo,
        [aliased],
    )

    assert original_scope.paths[0].object_id == aliased_scope.paths[0].object_id != ""
    assert original_scope.paths[0].object_scope == aliased_scope.paths[0].object_scope
    assert claim_scopes_conflict(
        str(root),
        original_displays,
        original_scope,
        str(root),
        aliased_displays,
        aliased_scope,
    )


def test_missing_semantic_source_retains_canonical_display_scope(tmp_path: Path) -> None:
    """A missing future source remains claimable without fabricated object identity."""
    repo = git_repo(tmp_path / "repo")
    semantic = semantic_scope_path("src/future.py", "run")

    _root, displays, scope = resolve_claim_scope_identity(repo, [semantic])

    assert displays == (semantic,)
    assert scope.paths[0].filesystem_path == semantic
    assert scope.paths[0].object_id == ""
    assert scope.paths[0].object_scope == "run"


def test_unreadable_semantic_source_identity_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable physical source cannot produce trusted semantic identity."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "worker.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    _commit(repo, "worker.py")
    resolved_source = source.resolve()
    original_stat = Path.stat

    def guarded_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == resolved_source and kwargs.get("follow_symlinks", True):
            raise PermissionError("fixture denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded_stat)

    with pytest.raises(PathIdentityError, match="source identity could not be read"):
        resolve_claim_scope_identity(
            repo,
            [semantic_scope_path("worker.py", "run")],
        )


def test_semantic_alias_into_reserved_scope_fails_closed(tmp_path: Path) -> None:
    """A valid display alias cannot rebuild identity inside the reserved segment."""
    repo = git_repo(tmp_path / "repo")
    reserved = repo / "src" / ".synapse-symbol" / "worker.py"
    reserved.parent.mkdir(parents=True)
    reserved.write_text("def run():\n    return 1\n", encoding="utf-8")
    alias = repo / "worker.py"
    try:
        alias.symlink_to(reserved.relative_to(repo))
    except OSError:
        pytest.skip("symbolic links are unavailable on this filesystem")
    _commit(repo, "src/.synapse-symbol/worker.py", "worker.py")

    with pytest.raises(PathIdentityError, match="filesystem identity is invalid"):
        resolve_claim_scope_identity(
            repo,
            [semantic_scope_path("worker.py", "run")],
        )


def test_semantic_source_without_inode_keeps_scope_without_object_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable inode disables alias comparison without losing the scope."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "worker.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    _commit(repo, "worker.py")
    resolved_source = source.resolve()
    original_stat = Path.stat

    def zero_inode_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        metadata = original_stat(path, *args, **kwargs)
        if path != resolved_source or not kwargs.get("follow_symlinks", True):
            return metadata
        values = list(metadata)
        values[1] = 0
        return os.stat_result(values)

    monkeypatch.setattr(Path, "stat", zero_inode_stat)

    _root, _displays, scope = resolve_claim_scope_identity(
        repo,
        [semantic_scope_path("worker.py", "run")],
    )

    assert scope.paths[0].object_id == ""
    assert scope.paths[0].object_scope == "run"


def test_maximum_semantic_scope_retains_bounded_object_scope(tmp_path: Path) -> None:
    """The longest valid semantic path produces a separately bounded object scope."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "a.py"
    alias = repo / "b.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    os.link(source, alias)
    _commit(repo, "a.py", "b.py")
    prefix = "a.py/.synapse-symbol/"
    symbol = "x" * (MAX_PATH_LENGTH - len(prefix))
    semantic = semantic_scope_path("a.py", symbol)

    _root, displays, scope = resolve_claim_scope_identity(repo, [semantic])

    assert displays == (semantic,)
    assert len(scope.paths[0].git_path) == MAX_PATH_LENGTH
    assert len(scope.paths[0].object_scope) < MAX_PATH_LENGTH
    assert ClaimScopeIdentity.from_dict(scope.as_dict()) == scope


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

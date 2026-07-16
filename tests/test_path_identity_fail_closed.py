# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed claim identity branch coverage
"""Exercise malformed wire values and local identity-resolution failures."""

from __future__ import annotations

import copy
import os
import platform
import uuid
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import git_repo
from synapse_channel.core import path_identity as core_identity
from synapse_channel.core.path_identity import (
    CanonicalPathIdentity,
    ClaimScopeIdentity,
    PathIdentityError,
    parse_optional_claim_scope_identity,
)
from synapse_channel.git import path_identity as git_identity
from synapse_channel.git.gitclaim import GitError, _default_git_runner


def _wire_identity() -> dict[str, Any]:
    """Return one fully populated valid wire identity."""
    return {
        "version": 1,
        "worktree_path": "/repo",
        "worktree_object_id": "1:2",
        "filesystem_namespace": "host:1",
        "case_sensitive": True,
        "paths": [
            {
                "git_path": "src/file.py",
                "filesystem_path": "src/file.py",
                "object_id": "1:3",
                "object_scope": "",
            }
        ],
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"paths": [{"git_path": None, "filesystem_path": "x"}]}, "must be a string"),
        ({"paths": [{"git_path": "", "filesystem_path": "x"}]}, "is invalid"),
        (
            {
                "paths": [
                    {
                        "git_path": "src/file.py",
                        "filesystem_path": "src/file.py",
                        "object_id": "not an object key",
                    }
                ]
            },
            "object_id is invalid",
        ),
        (
            {
                "paths": [
                    {
                        "git_path": "src/file.py",
                        "filesystem_path": "src/file.py",
                        "object_id": "1:3",
                        "object_scope": "../run",
                    }
                ]
            },
            "object_scope must be canonical",
        ),
        (
            {
                "paths": [
                    {
                        "git_path": "src/file.py",
                        "filesystem_path": "src/file.py",
                        "object_id": "1:3",
                        "object_scope": "run",
                    }
                ]
            },
            "object_scope requires a semantic git_path",
        ),
        (
            {
                "paths": [
                    {
                        "git_path": "src/file.py/.synapse-symbol/run",
                        "filesystem_path": "src/file.py/.synapse-symbol/run",
                        "object_id": "1:3",
                        "object_scope": "other",
                    }
                ]
            },
            "object_scope does not match git_path",
        ),
        ({"worktree_path": "/repo/"}, "worktree_path is not canonical"),
        ({"paths": {}}, "list of mappings"),
    ],
)
def test_wire_identity_rejects_each_invalid_field(mutation: dict[str, Any], message: str) -> None:
    """Every bounded identity field fails before a partial object is built."""
    payload = copy.deepcopy(_wire_identity())
    payload.update(mutation)
    with pytest.raises(PathIdentityError, match=message):
        ClaimScopeIdentity.from_dict(payload)


def test_wire_worktree_accepts_drive_root_and_rejects_noncanonical_separators() -> None:
    payload = _wire_identity()
    payload["worktree_path"] = "C:/"
    assert ClaimScopeIdentity.from_dict(payload).worktree_path == "C:/"
    payload["worktree_path"] = r"C:\repo"
    with pytest.raises(PathIdentityError, match="worktree_path is not canonical"):
        ClaimScopeIdentity.from_dict(payload)


def test_display_alignment_rejects_count_and_spelling_mismatch() -> None:
    """Identity rows cannot be detached from their human-readable displays."""
    identity = ClaimScopeIdentity.from_dict(_wire_identity())
    assert not identity.validates_display_paths(())
    assert not identity.validates_display_paths(("src/other.py",))
    assert identity.validates_display_scope("/repo", ("src/file.py",))
    assert not identity.validates_display_scope("/other", ("src/file.py",))


def test_optional_identity_distinguishes_absent_from_present_invalid() -> None:
    """Durable and snapshot readers may support legacy absence, never invalid presence."""
    assert parse_optional_claim_scope_identity({}) is None
    with pytest.raises(PathIdentityError, match="must be a mapping"):
        parse_optional_claim_scope_identity({"path_identity": "invalid"})


def test_scope_comparison_covers_legacy_and_canonical_branches() -> None:
    """Worktree, whole-tree, ancestry, object, and legacy paths stay explicit."""
    sensitive = ClaimScopeIdentity.from_dict(_wire_identity())
    insensitive = ClaimScopeIdentity(
        worktree_path="/repo",
        case_sensitive=False,
        paths=(CanonicalPathIdentity("src/file.py", "real/file.py", "1:3"),),
    )
    other = ClaimScopeIdentity(
        worktree_path="/other",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("src/file.py", "src/file.py"),),
    )

    assert core_identity.claim_worktrees_match("/repo", None, "/repo", None)
    assert not core_identity.claim_worktrees_match("/repo", None, "/other", None)
    assert core_identity.claim_worktrees_match("/REPO", insensitive, "/repo", sensitive)
    assert core_identity.claim_worktrees_match("/repo", sensitive, "/repo", None)
    assert not core_identity.claim_worktrees_match("/repo", sensitive, "/other", other)
    recreated = ClaimScopeIdentity(
        worktree_path="/repo",
        worktree_object_id="9:9",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=sensitive.paths,
    )
    remote = ClaimScopeIdentity(
        worktree_path="/repo",
        worktree_object_id=sensitive.worktree_object_id,
        filesystem_namespace="host:2",
        case_sensitive=True,
        paths=sensitive.paths,
    )
    assert not core_identity.claim_worktrees_match("/repo", sensitive, "/repo", recreated)
    assert not core_identity.claim_worktrees_match("/repo", sensitive, "/repo", remote)
    assert not core_identity.claim_scopes_conflict(
        "/repo", ("src",), sensitive, "/other", ("src",), other
    )
    assert core_identity.claim_scopes_conflict(
        "/repo", (), sensitive, "/repo", ("unrelated",), None
    )
    assert core_identity.claim_scopes_conflict(
        "/repo", ("src",), None, "/repo", ("src/file.py",), None
    )
    assert core_identity.claim_scopes_conflict(
        "/repo", ("src/file.py",), sensitive, "/repo", ("SRC/FILE.PY",), insensitive
    )
    assert core_identity.claim_scopes_conflict(
        "/repo", ("src/file.py",), sensitive, "/repo", ("src/file.py",), None
    )
    assert core_identity.comparison_worktree("/", case_sensitive=True) == "/"
    assert core_identity.comparison_worktree("/repo ", case_sensitive=True) == "/repo "
    assert core_identity.comparison_worktree(r"/repo\name", case_sensitive=True) == r"/repo\name"
    assert core_identity.comparison_worktree(r"C:\repo", case_sensitive=False) == "c:/repo"
    assert (
        core_identity.comparison_worktree(r"\\server\share", case_sensitive=True)
        == "//server/share"
    )

    row = sensitive.paths[0]
    alias = CanonicalPathIdentity("alias.py", "alias.py", "1:3")
    assert core_identity.claim_scope_covers_path(
        "src", None, "src/file.py", None, case_sensitive=None
    )
    assert not core_identity.claim_scope_covers_path(
        "src", None, "docs/file.py", None, case_sensitive=None
    )
    assert core_identity.claim_scope_covers_path(
        "src/file.py",
        row,
        "alias.py",
        alias,
        case_sensitive=True,
        object_identity_safe=True,
    )
    assert core_identity.claim_scope_covers_path(
        "SRC", None, "src/file.py", row, case_sensitive=False
    )


def test_object_ids_require_matching_worktree_namespace() -> None:
    """Coincident device/inode values from different roots never alias."""
    first = ClaimScopeIdentity(
        worktree_path="/repo",
        worktree_object_id="1:100",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("a.py", "a.py", "1:2"),),
    )
    second = ClaimScopeIdentity(
        worktree_path="/repo",
        worktree_object_id="1:100",
        filesystem_namespace="host:2",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("b.py", "b.py", "1:2"),),
    )
    assert not core_identity.claim_scopes_conflict(
        "/repo", ("a.py",), first, "/repo", ("b.py",), second
    )
    assert not core_identity.claim_scope_covers_path(
        "a.py",
        first.paths[0],
        "b.py",
        second.paths[0],
        case_sensitive=True,
        object_identity_safe=core_identity.claim_object_ids_comparable(first, second),
    )


def test_same_proven_root_alias_is_conflict_only() -> None:
    """Bind-mount labels contend but remain distinct authorization roots."""
    first = ClaimScopeIdentity(
        worktree_path="/mnt/a",
        worktree_object_id="1:100",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("x.py", "x.py"),),
    )
    second = ClaimScopeIdentity(
        worktree_path="/mnt/b",
        worktree_object_id="1:100",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("x.py", "x.py"),),
    )
    assert not core_identity.claim_worktrees_match("/mnt/a", first, "/mnt/b", second)
    assert core_identity.claim_scopes_conflict(
        "/mnt/a", ("x.py",), first, "/mnt/b", ("x.py",), second
    )


def test_canonical_root_and_case_helpers_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OS canonicalisation and case probes never promote uncertainty to identity."""
    ordinary_file = tmp_path / "ordinary.txt"
    ordinary_file.write_text("x", encoding="utf-8")
    with pytest.raises(PathIdentityError, match="not a directory"):
        git_identity._canonical_root(ordinary_file)

    monkeypatch.setattr(
        os.path,
        "realpath",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError()),
    )
    with pytest.raises(PathIdentityError, match="cannot be resolved"):
        git_identity._canonical_root(tmp_path)


def test_case_probe_distinguishes_sensitive_insensitive_and_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real entry is authoritative and OS errors remain unknown."""
    assert git_identity._alternate_case(Path("123")) is None
    assert git_identity._case_probe(Path("123")) is None
    assert git_identity._alternate_case(Path("Alpha")) == Path("alpha")

    monkeypatch.setattr(os.path, "lexists", lambda _path: False)
    assert git_identity._case_probe(Path("alpha")) is True
    monkeypatch.setattr(os.path, "lexists", lambda _path: True)
    monkeypatch.setattr(os.path, "samefile", lambda _left, _right: True)
    assert git_identity._case_probe(Path("alpha")) is False
    monkeypatch.setattr(os.path, "samefile", lambda _left, _right: False)
    assert git_identity._case_probe(Path("alpha")) is True
    monkeypatch.setattr(os, "scandir", lambda _path: (_ for _ in ()).throw(PermissionError()))
    assert git_identity._case_probe(Path("alpha")) is True
    monkeypatch.setattr(
        os.path,
        "samefile",
        lambda _left, _right: (_ for _ in ()).throw(PermissionError()),
    )
    assert git_identity._case_probe(Path("alpha")) is None


def test_filesystem_namespace_override_is_opaque_and_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured shared-filesystem namespaces cross the wire only as bounded hashes."""
    monkeypatch.setenv("SYNAPSE_FILESYSTEM_NAMESPACE", "cluster-a")
    first = git_identity._filesystem_namespace()
    monkeypatch.setenv("SYNAPSE_FILESYSTEM_NAMESPACE", "cluster-b")
    second = git_identity._filesystem_namespace()
    assert first.startswith("sha256:")
    assert len(first) == 71
    assert first != second
    assert "cluster" not in first


def test_filesystem_namespace_node_fallback_is_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosts without a readable machine id still emit one opaque provenance token."""
    monkeypatch.delenv("SYNAPSE_FILESYSTEM_NAMESPACE", raising=False)
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _path, **_kwargs: (_ for _ in ()).throw(PermissionError()),
    )
    monkeypatch.setattr(platform, "node", lambda: "fixture-host")
    monkeypatch.setattr(uuid, "getnode", lambda: 0x1234)
    assert git_identity._filesystem_namespace() == git_identity._filesystem_namespace()
    assert git_identity._filesystem_namespace().startswith("sha256:")


def test_filesystem_namespace_empty_machine_ids_reach_node_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNAPSE_FILESYSTEM_NAMESPACE", raising=False)
    monkeypatch.setattr(Path, "read_text", lambda _path, **_kwargs: "")
    monkeypatch.setattr(platform, "node", lambda: "fixture-host")
    monkeypatch.setattr(uuid, "getnode", lambda: 0x1234)
    assert git_identity._filesystem_namespace().startswith("sha256:")


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("true", False), ("false", True)],
)
def test_case_detection_uses_git_then_host_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: bool,
) -> None:
    """Empty roots use a documented Git policy when filesystem proof is absent."""
    monkeypatch.setattr(git_identity, "_case_probe", lambda _path: None)
    assert (
        git_identity.detect_case_sensitivity(tmp_path, runner=lambda _args: configured) is expected
    )


def test_case_detection_rejects_unknown_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown case behavior cannot mint a boolean authorization policy."""
    monkeypatch.setattr(git_identity, "_case_probe", lambda _path: None)
    with pytest.raises(PathIdentityError, match="could not be established"):
        git_identity.detect_case_sensitivity(tmp_path, runner=lambda _args: "")


def test_case_detection_survives_scan_and_git_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreadable scans and Git config errors fall back without a write probe."""
    monkeypatch.setattr(os, "scandir", lambda _path: (_ for _ in ()).throw(PermissionError()))
    monkeypatch.setattr(git_identity, "_case_probe", lambda _path: None)

    def failing_runner(_args: list[str]) -> str:
        raise GitError("unavailable")

    with pytest.raises(PathIdentityError, match="could not be established"):
        git_identity.detect_case_sensitivity(tmp_path, runner=failing_runner)


def test_case_detection_uses_root_probe_and_bounds_directory_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read-only probe can use the root and never scans an unbounded directory."""
    monkeypatch.setattr(git_identity, "_case_probe", lambda path: path == tmp_path)
    assert git_identity.detect_case_sensitivity(tmp_path, runner=lambda _args: "")

    for index in range(65):
        (tmp_path / str(index)).write_text("x", encoding="utf-8")
    seen = 0

    def unknown_probe(_path: Path) -> None:
        nonlocal seen
        seen += 1
        return None

    monkeypatch.setattr(git_identity, "_case_probe", unknown_probe)
    git_identity.detect_case_sensitivity(tmp_path, runner=lambda _args: "false")
    assert seen == 65


@pytest.mark.parametrize("raw", ["../escape\0", "a//b\0", "same\0same\0"])
def test_git_index_rejects_noncanonical_or_ambiguous_paths(tmp_path: Path, raw: str) -> None:
    """An index entry must already be a unique canonical repository path."""
    with pytest.raises(PathIdentityError, match="ambiguous or invalid"):
        git_identity._index_paths(tmp_path, runner=lambda _args: raw)


def test_git_index_read_error_is_controlled(tmp_path: Path) -> None:
    """A Git failure never degrades to an empty index."""

    def failing_runner(_args: list[str]) -> str:
        raise GitError("unavailable")

    with pytest.raises(PathIdentityError, match="could not be read"):
        git_identity._index_paths(tmp_path, runner=failing_runner)


def test_git_index_validation_has_no_claim_scope_count_cap(tmp_path: Path) -> None:
    """A large repository index remains readable for one narrow claim."""
    paths = tuple(f"src/file-{index}.py" for index in range(600))
    assert (
        git_identity._index_paths(tmp_path, runner=lambda _args: "\0".join((*paths, ""))) == paths
    )


def test_current_repository_narrow_identity_handles_large_index() -> None:
    """The production checkout can resolve one path despite its large tracked index."""
    root = Path(__file__).resolve().parents[1]
    _, displays, identity = git_identity.resolve_claim_scope_identity(root, ("README.md",))
    assert displays == ("README.md",)
    assert identity.paths[0].git_path == "README.md"


def test_case_insensitive_git_spelling_handles_every_candidate_shape() -> None:
    """Index spelling is exact, uniquely folded, new, or explicitly ambiguous."""
    prefixes = git_identity._prefixes(("Src/File.py",))
    assert (
        git_identity._git_spelling("Src/File.py", index_prefixes=prefixes, case_sensitive=False)
        == "Src/File.py"
    )
    assert (
        git_identity._git_spelling("src/file.py", index_prefixes=prefixes, case_sensitive=False)
        == "Src/File.py"
    )
    assert (
        git_identity._git_spelling("new/file.py", index_prefixes=prefixes, case_sensitive=False)
        == "new/file.py"
    )
    ambiguous = git_identity._prefixes(("Src/File.py", "SRC/Other.py"))
    with pytest.raises(PathIdentityError, match="ambiguous case-insensitive"):
        git_identity._git_spelling("src", index_prefixes=ambiguous, case_sensitive=False)
    assert (
        git_identity._git_spelling("Mixed/Case.py", index_prefixes=prefixes, case_sensitive=True)
        == "Mixed/Case.py"
    )


def test_missing_tail_and_object_id_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing leaves inherit a strict anchor and carry no invented object id."""
    missing = tmp_path / "new" / "file.py"
    assert git_identity._resolved_with_missing_tail(missing) == missing
    assert git_identity._object_id(missing) == ""

    original_stat = Path.stat

    def denied_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == missing:
            raise PermissionError
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", denied_stat)
    with pytest.raises(PathIdentityError, match="object identity"):
        git_identity._object_id(missing)


def test_zero_inode_never_becomes_an_alias_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Network filesystems that report unavailable inode zero disable alias matching."""
    target = tmp_path / "target"
    target.write_text("x", encoding="utf-8")
    metadata = target.stat()
    monkeypatch.setattr(
        Path,
        "stat",
        lambda _path: os.stat_result(
            (
                metadata.st_mode,
                0,
                metadata.st_dev,
                metadata.st_nlink,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_size,
                metadata.st_atime,
                metadata.st_mtime,
                metadata.st_ctime,
            )
        ),
    )
    assert git_identity._object_id(target) == ""


def test_path_anchor_and_alias_errors_are_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing anchors, unreadable components, and invalid aliases have one error surface."""
    monkeypatch.setattr(Path, "lstat", lambda _path: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(PathIdentityError, match="no resolvable"):
        git_identity._resolved_with_missing_tail(tmp_path / "x")

    monkeypatch.setattr(Path, "lstat", lambda _path: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PathIdentityError, match="unreadable"):
        git_identity._resolved_with_missing_tail(tmp_path / "x")

    monkeypatch.setattr(Path, "lstat", lambda _path: None)
    monkeypatch.setattr(
        os.path,
        "realpath",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError()),
    )
    with pytest.raises(PathIdentityError, match="invalid filesystem alias"):
        git_identity._resolved_with_missing_tail(tmp_path / "x")


def test_resolver_rejects_traversal_and_normalisation_collisions(tmp_path: Path) -> None:
    """Bounded intent cannot widen or collapse before identity derivation."""
    repo = git_repo(tmp_path / "repo")
    with pytest.raises(PathIdentityError, match="bounded"):
        git_identity.resolve_claim_scope_identity(repo, ("../outside",))
    with pytest.raises(PathIdentityError, match="canonical display"):
        git_identity.resolve_claim_scope_identity(repo, ("new/file.py", "new//file.py"))


def test_resolver_rejects_legal_whitespace_path_before_hardlink_alias_bypass(
    tmp_path: Path,
) -> None:
    """Legacy display normalization cannot detach a legal Git path from its inode."""
    repo = git_repo(tmp_path / "repo")
    spaced = repo / " file.py"
    alias = repo / "alias.py"
    spaced.write_text("VALUE = 1\n", encoding="utf-8")
    os.link(spaced, alias)
    _default_git_runner(["-C", str(repo), "add", "--", " file.py", "alias.py"])
    with pytest.raises(PathIdentityError, match="canonical display"):
        git_identity.resolve_claim_scope_identity(repo, (" file.py",))


def test_resolver_refuses_nonprintable_canonical_worktree(tmp_path: Path) -> None:
    """A local root that cannot cross JSON as a bounded identity is denied locally."""
    repo = git_repo(tmp_path / "line\nbreak")
    with pytest.raises(PathIdentityError, match="worktree_path is invalid"):
        git_identity.resolve_claim_scope_identity(repo, ("README.md",))


def test_resolver_refuses_root_metadata_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worktree without stable root metadata cannot mint an identity."""
    repo = git_repo(tmp_path / "repo")
    original_stat = Path.stat
    root_calls = 0

    def failing_root_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        nonlocal root_calls
        if path == repo:
            root_calls += 1
            if root_calls > 2:
                raise PermissionError
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", failing_root_stat)
    with pytest.raises(PathIdentityError, match="worktree identity"):
        git_identity.resolve_claim_scope_identity(repo, ())


def test_resolver_refuses_internal_display_misalignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final invariant remains enforced even after successful OS resolution."""
    repo = git_repo(tmp_path / "repo")
    monkeypatch.setattr(ClaimScopeIdentity, "validates_display_paths", lambda *_args: False)
    with pytest.raises(PathIdentityError, match="does not align"):
        git_identity.resolve_claim_scope_identity(repo, ("README.md",))

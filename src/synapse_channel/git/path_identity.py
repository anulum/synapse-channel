# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — derive Git and filesystem claim identities locally
"""Derive portable claim identities from one local Git worktree.

The hub must never read a repository.  This client-side module therefore binds
display scopes to the Git index and live filesystem before a claim or enforcement
decision crosses the wire.  Existing components are resolved strictly (including
Windows junctions and 8.3 names through :func:`os.path.realpath`), missing tails
remain claimable, symlink escapes fail closed, and existing objects carry a
device/inode key so hard-link aliases contend.
"""

from __future__ import annotations

import errno
import hashlib
import os
import platform
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path

from synapse_channel.core.path_identity import (
    CanonicalPathIdentity,
    ClaimScopeIdentity,
    PathIdentityError,
    comparison_path,
    comparison_worktree,
)
from synapse_channel.core.scoping import normalize_path, normalize_paths
from synapse_channel.git.gitclaim import GitError, GitRunner, _default_git_runner
from synapse_channel.git.semantic_scope import parse_semantic_scope, semantic_scope_path

_CASE_PROBE_LIMIT = 64


def _filesystem_namespace() -> str:
    """Return an opaque stable host namespace for local filesystem object IDs."""
    configured = os.environ.get("SYNAPSE_FILESYSTEM_NAMESPACE", "").strip()
    if configured:
        source = f"configured:{configured}"
    else:
        machine_id = ""
        for location in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
            try:
                machine_id = location.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if machine_id:
                break
        source = (
            f"machine-id:{machine_id}"
            if machine_id
            else f"node:{platform.node()}:{uuid.getnode():012x}"
        )
    digest = hashlib.sha256(f"synapse-channel-path-identity:{source}".encode()).hexdigest()
    return f"sha256:{digest}"


def _canonical_root(root: Path) -> Path:
    """Return the strict OS-canonical worktree root."""
    try:
        canonical = Path(os.path.realpath(root, strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathIdentityError("claim worktree cannot be resolved canonically") from exc
    if not canonical.is_dir():
        raise PathIdentityError("claim worktree is not a directory")
    return canonical


def _alternate_case(path: Path) -> Path | None:
    """Return the same path with one ASCII letter's case toggled."""
    name = path.name
    for index, char in enumerate(name):
        if "a" <= char <= "z":
            return path.with_name(name[:index] + char.upper() + name[index + 1 :])
        if "A" <= char <= "Z":
            return path.with_name(name[:index] + char.lower() + name[index + 1 :])
    return None


def _case_probe(path: Path) -> bool | None:
    """Return actual case sensitivity for one entry, or ``None`` without evidence."""
    alternate = _alternate_case(path)
    if alternate is None or alternate == path:
        return None
    try:
        if not os.path.lexists(alternate):
            return True
        try:
            exact_names = {entry.name for entry in os.scandir(path.parent)}
        except OSError:
            exact_names = set()
        if path.name in exact_names and alternate.name in exact_names:
            return True
        return not os.path.samefile(path, alternate)
    except OSError:
        return None


def detect_case_sensitivity(root: Path, *, runner: GitRunner = _default_git_runner) -> bool:
    """Detect the worktree filesystem's path-comparison policy without writes.

    A real directory entry is authoritative and supports case-sensitive Windows
    directories as well as case-sensitive or insensitive macOS volumes.  Git's
    ``core.ignorecase`` is a fallback for an empty/unreadable root. If neither
    source proves a policy, identity derivation stops instead of guessing a bool
    that could be unsafe for either conflict detection or edit authorization.
    """
    canonical = _canonical_root(root)
    try:
        with os.scandir(canonical) as entries:
            for index, entry in enumerate(entries):
                if index >= _CASE_PROBE_LIMIT:
                    break
                result = _case_probe(Path(entry.path))
                if result is not None:
                    return result
    except OSError:
        pass
    root_result = _case_probe(canonical)
    if root_result is not None:
        return root_result
    try:
        configured = runner(
            ["-C", str(canonical), "config", "--bool", "--get", "core.ignorecase"]
        ).strip()
    except (GitError, OSError, RuntimeError):
        configured = ""
    if configured == "true":
        return False
    if configured == "false":
        return True
    raise PathIdentityError("worktree case-sensitivity policy could not be established")


def _index_paths(root: Path, *, runner: GitRunner) -> tuple[str, ...]:
    """Return validated Git-index path spelling in index order."""
    try:
        raw = runner(["-C", str(root), "ls-files", "-z", "--cached"])
    except (GitError, OSError, RuntimeError) as exc:
        raise PathIdentityError("Git index paths could not be read") from exc
    paths = tuple(path for path in raw.split("\0") if path)
    if len(set(paths)) != len(paths) or any(
        not path or normalize_path(path) != path for path in paths
    ):
        raise PathIdentityError("Git index returned ambiguous or invalid paths")
    return paths


def _prefixes(paths: Iterable[str]) -> dict[str, set[str]]:
    """Index every component prefix by its case-folded NFC spelling."""
    result: dict[str, set[str]] = {}
    for path in paths:
        parts = path.replace("\\", "/").split("/")
        for length in range(1, len(parts) + 1):
            prefix = unicodedata.normalize("NFC", "/".join(parts[:length]))
            result.setdefault(prefix.casefold(), set()).add(prefix)
    return result


def _git_spelling(
    display_path: str,
    *,
    index_prefixes: dict[str, set[str]],
    case_sensitive: bool,
) -> str:
    """Return the index-canonical component spelling for one display path."""
    requested = unicodedata.normalize("NFC", display_path)
    if case_sensitive:
        return requested
    parts = requested.split("/")
    selected: list[str] = []
    for length in range(1, len(parts) + 1):
        requested_prefix = "/".join(parts[:length])
        candidates = index_prefixes.get(requested_prefix.casefold(), set())
        if requested_prefix in candidates:
            canonical = requested_prefix
        elif len(candidates) == 1:
            canonical = next(iter(candidates))
        elif len(candidates) > 1:
            raise PathIdentityError("Git index has an ambiguous case-insensitive path")
        else:
            selected.append(parts[length - 1])
            continue
        selected = canonical.split("/")
    return "/".join(selected)


def _resolved_with_missing_tail(path: Path) -> Path:
    """Resolve an existing anchor strictly and append a genuinely missing tail."""
    anchor = path
    missing: list[str] = []
    while True:
        try:
            anchor.lstat()
            break
        except OSError as exc:
            # A missing path and a path too long to exist are both a genuinely
            # missing tail, not an unreadable component: an over-length path cannot
            # name a real tracked file, so it is absent. This keeps resolution
            # consistent where PATH_MAX differs (macOS 1024 vs Linux 4096) and stays
            # fail-closed (a missing path has no covering claim). FileNotFoundError
            # is matched by type (its errno may be unset); other errors (EACCES,
            # EIO, ELOOP) remain unreadable.
            is_missing_tail = isinstance(exc, FileNotFoundError) or exc.errno == errno.ENAMETOOLONG
            if not is_missing_tail:
                raise PathIdentityError("claim path contains an unreadable component") from exc
            if anchor == anchor.parent:
                raise PathIdentityError("claim path has no resolvable filesystem anchor") from None
            missing.append(anchor.name)
            anchor = anchor.parent
    try:
        resolved = Path(os.path.realpath(anchor, strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathIdentityError("claim path contains an invalid filesystem alias") from exc
    return resolved.joinpath(*reversed(missing))


def _object_id(path: Path) -> str:
    """Return a stable local object key for an existing file or directory."""
    try:
        metadata = path.stat()
    except OSError as exc:
        # A missing path and an over-length path are both a genuinely absent
        # object, not an unreadable one: an over-PATH_MAX name cannot name a real
        # tracked file (macOS 1024 vs Linux 4096), so it has no object identity.
        # This mirrors _resolved_with_missing_tail and stays fail-closed — an
        # empty object id yields no covering claim, so allowed=False either way.
        # FileNotFoundError is matched by type (its errno may be unset);
        # ENAMETOOLONG by errno. Other errors (EACCES, EIO) remain unreadable.
        if isinstance(exc, FileNotFoundError) or exc.errno == errno.ENAMETOOLONG:
            return ""
        raise PathIdentityError("claim path object identity could not be read") from exc
    if metadata.st_ino <= 0:
        return ""
    return f"{metadata.st_dev:x}:{metadata.st_ino:x}"


def _semantic_filesystem_scope(relative: str, symbol: str) -> str:
    """Return the physical-source-relative scope for one declaration."""
    try:
        return semantic_scope_path(relative, symbol)
    except ValueError as exc:
        raise PathIdentityError("semantic claim filesystem identity is invalid") from exc


def _semantic_object_identity(resolved: Path, symbol: str) -> tuple[str, str]:
    """Return the source-object key and canonical semantic sub-scope."""
    try:
        metadata = resolved.stat()
    except OSError as exc:
        # As in _object_id: a missing path and an over-length path are both a
        # genuinely absent source object, so an over-PATH_MAX semantic source has
        # no object identity (fail-closed: no identity -> no covering claim).
        # FileNotFoundError by type, ENAMETOOLONG by errno; others stay unreadable.
        if isinstance(exc, FileNotFoundError) or exc.errno == errno.ENAMETOOLONG:
            object_id = ""
        else:
            raise PathIdentityError("semantic claim source identity could not be read") from exc
    else:
        object_id = f"{metadata.st_dev:x}:{metadata.st_ino:x}" if metadata.st_ino > 0 else ""
    encoded = semantic_scope_path("_", symbol)
    marker = "_/.synapse-symbol/"
    return object_id, encoded.removeprefix(marker)


def _resolved_claim_path(root: Path, display: str) -> tuple[str, str, str]:
    """Return filesystem spelling, object identity, and object scope.

    Semantic claim paths are coordination descendants, not real children of the
    source file. Their physical source is resolved for alias and escape checks,
    then the synthetic suffix is rebuilt over that canonical source spelling.
    Existing semantic sources retain the physical object's identity plus an
    object-relative declaration scope. This keeps whole-file aliases and
    declaration ancestry conflicting across hard links while sibling symbols
    remain independently claimable.
    """
    semantic = parse_semantic_scope(display)
    physical_display = semantic.source if semantic is not None else display
    resolved = _resolved_with_missing_tail(root / physical_display)
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise PathIdentityError("claim path resolves outside the Git worktree") from exc
    if semantic is not None:
        object_id, object_scope = _semantic_object_identity(resolved, semantic.symbol)
        return (
            _semantic_filesystem_scope(relative, semantic.symbol),
            object_id,
            object_scope,
        )
    return relative, _object_id(resolved), ""


def resolve_claim_scope_identity(
    root: Path,
    paths: Sequence[str],
    *,
    runner: GitRunner = _default_git_runner,
) -> tuple[Path, tuple[str, ...], ClaimScopeIdentity]:
    """Resolve canonical display paths and their portable claim identity.

    Returns the strict canonical root, the normalised displays sent as ``paths``,
    and the aligned additive identity.  Invalid/traversal-like bounded paths are
    refused instead of silently turning an explicitly bounded Git claim into a
    whole-worktree claim.
    """
    canonical_root = _canonical_root(root)
    raw_paths = tuple(paths)
    display_paths = normalize_paths(raw_paths)
    if paths and (not display_paths or "" in display_paths):
        raise PathIdentityError("claim paths must be bounded repository-relative paths")
    if display_paths != raw_paths:
        raise PathIdentityError("claim paths must already be unique canonical display paths")
    case_sensitive = detect_case_sensitivity(canonical_root, runner=runner)
    index_prefixes = _prefixes(_index_paths(canonical_root, runner=runner))
    identities: list[CanonicalPathIdentity] = []
    for display in display_paths:
        git_spelling = _git_spelling(
            display,
            index_prefixes=index_prefixes,
            case_sensitive=case_sensitive,
        )
        filesystem_path, object_id, object_scope = _resolved_claim_path(
            canonical_root,
            display,
        )
        identities.append(
            CanonicalPathIdentity(
                git_path=comparison_path(git_spelling, case_sensitive=case_sensitive),
                filesystem_path=comparison_path(
                    filesystem_path,
                    case_sensitive=case_sensitive,
                ),
                object_id=object_id,
                object_scope=object_scope,
            )
        )
    try:
        root_metadata = canonical_root.stat()
    except OSError as exc:
        raise PathIdentityError("claim worktree identity could not be read") from exc
    scope = ClaimScopeIdentity(
        worktree_path=comparison_worktree(canonical_root.as_posix(), case_sensitive=case_sensitive),
        worktree_object_id=(
            f"{root_metadata.st_dev:x}:{root_metadata.st_ino:x}" if root_metadata.st_ino > 0 else ""
        ),
        filesystem_namespace=_filesystem_namespace(),
        case_sensitive=case_sensitive,
        paths=tuple(identities),
    )
    scope = ClaimScopeIdentity.from_dict(scope.as_dict())
    if not scope.validates_display_scope(canonical_root.as_posix(), display_paths):
        raise PathIdentityError("claim path identity does not align with display scope")
    return canonical_root, display_paths, scope

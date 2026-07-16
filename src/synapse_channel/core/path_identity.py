# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — portable filesystem-canonical claim identities
"""Validate and compare portable filesystem-canonical claim identities.

Claim ``paths`` remain human-readable repository-relative display values.  A
git-aware client may additionally attach one versioned :class:`ClaimScopeIdentity`
whose comparison values bind those displays to Git-index spelling, resolved
filesystem spelling, hard-link identity, worktree identity, Unicode NFC, and the
worktree's case-sensitivity policy.  The hub remains filesystem-agnostic: it only
validates and compares client-derived values.

The optional field is additive.  New identities compare with legacy claims by
canonicalising the legacy display paths under the new claim's case policy.  Two
legacy claims retain the historical literal-path behaviour.  This makes mixed
fleets conservative without changing what old clients render or parse.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.scoping import MAX_PATH_LENGTH, normalize_path, paths_overlap

PATH_IDENTITY_VERSION = 1
"""Current additive claim-path identity schema version."""

MAX_OBJECT_ID_LENGTH = 160
"""Maximum portable filesystem object-identity length accepted from a client."""

_OBJECT_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SEMANTIC_SCOPE_MARKER = "/.synapse-symbol/"


class PathIdentityError(SynapseError, ValueError):
    """A claim carries a malformed or internally inconsistent path identity."""

    code = "path_identity"


def comparison_path(path: str, *, case_sensitive: bool) -> str:
    """Return a repository-relative comparison path in Unicode NFC.

    Traversal-like or absolute input follows the existing conservative scope
    algebra and becomes the worktree root.  Case-folding is applied only when
    the client established that the worktree filesystem is case-insensitive.
    """
    value = unicodedata.normalize("NFC", normalize_path(path))
    return value if case_sensitive else value.casefold()


def comparison_worktree(path: str, *, case_sensitive: bool) -> str:
    """Return an NFC worktree label without collapsing legal path characters."""
    value = unicodedata.normalize("NFC", path)
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
        value = value.replace("\\", "/")
    return value if case_sensitive else value.casefold()


def _identity_string(value: object, *, field: str, allow_empty: bool = False) -> str:
    """Validate one printable bounded identity string."""
    if not isinstance(value, str):
        raise PathIdentityError(f"claim path identity {field} must be a string")
    if (not value and not allow_empty) or len(value) > MAX_PATH_LENGTH or not value.isprintable():
        raise PathIdentityError(f"claim path identity {field} is invalid")
    return value


def _object_identity(value: object, *, field: str) -> str:
    """Validate one optional device/object identity without logging its value."""
    text = _identity_string(value, field=field, allow_empty=True)
    if text and (len(text) > MAX_OBJECT_ID_LENGTH or _OBJECT_ID.fullmatch(text) is None):
        raise PathIdentityError(f"claim path identity {field} is invalid")
    return text


@dataclass(frozen=True)
class CanonicalPathIdentity:
    """Canonical comparison values for one displayed claim path.

    ``git_path`` is derived from the index's component spelling.  ``filesystem_path``
    is the resolved path relative to the canonical worktree.  ``object_id`` is
    present only for an existing filesystem object and detects hard-link aliases.
    ``object_scope`` is empty for the whole object and otherwise names one
    semantic descendant within it. It refines conflict comparison only; neither
    field grants edit or release authority.
    """

    git_path: str
    filesystem_path: str
    object_id: str = ""
    object_scope: str = ""

    def as_dict(self) -> dict[str, str]:
        """Return the additive wire representation for this path."""
        return {
            "git_path": self.git_path,
            "filesystem_path": self.filesystem_path,
            "object_id": self.object_id,
            "object_scope": self.object_scope,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CanonicalPathIdentity:
        """Validate and rebuild one path identity from a wire mapping."""
        git_path = _identity_string(data.get("git_path"), field="git_path")
        filesystem_path = _identity_string(
            data.get("filesystem_path"), field="filesystem_path", allow_empty=True
        )
        if (
            normalize_path(git_path) != git_path
            or normalize_path(filesystem_path) != filesystem_path
        ):
            raise PathIdentityError("claim path identity paths must be canonical and relative")
        object_scope = _identity_string(
            data.get("object_scope", ""),
            field="object_scope",
            allow_empty=True,
        )
        if normalize_path(object_scope) != object_scope:
            raise PathIdentityError("claim path identity object_scope must be canonical")
        return cls(
            git_path=git_path,
            filesystem_path=filesystem_path,
            object_id=_object_identity(data.get("object_id", ""), field="object_id"),
            object_scope=object_scope,
        )


@dataclass(frozen=True)
class ClaimScopeIdentity:
    """Versioned canonical identity for one worktree-scoped claim.

    Attributes
    ----------
    worktree_path:
        OS-canonical worktree path under the originating filesystem's case
        policy. It separates unrelated repositories before path comparison.
    case_sensitive:
        Whether path case is significant in the originating worktree.
    paths:
        Canonical rows aligned one-to-one with the claim's display paths.
    worktree_object_id:
        Optional local device/object key retained for diagnostics and replay.
    filesystem_namespace:
        Opaque host namespace proving when local device/object keys are comparable.
    version:
        Additive identity schema version.
    """

    worktree_path: str
    case_sensitive: bool
    paths: tuple[CanonicalPathIdentity, ...]
    worktree_object_id: str = ""
    filesystem_namespace: str = ""
    version: int = PATH_IDENTITY_VERSION

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable additive claim field."""
        return {
            "version": self.version,
            "worktree_path": self.worktree_path,
            "worktree_object_id": self.worktree_object_id,
            "filesystem_namespace": self.filesystem_namespace,
            "case_sensitive": self.case_sensitive,
            "paths": [path.as_dict() for path in self.paths],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ClaimScopeIdentity:
        """Validate and rebuild a scope identity from a wire or journal mapping."""
        version = data.get("version")
        if type(version) is not int or version != PATH_IDENTITY_VERSION:
            raise PathIdentityError("unsupported claim path identity version")
        case_sensitive = data.get("case_sensitive")
        if type(case_sensitive) is not bool:
            raise PathIdentityError("claim path identity case_sensitive must be a boolean")
        worktree_path = _identity_string(data.get("worktree_path"), field="worktree_path")
        if (
            worktree_path.endswith("/")
            and worktree_path != "/"
            and re.fullmatch(r"[A-Za-z]:/", worktree_path) is None
        ):
            raise PathIdentityError("claim path identity worktree_path is not canonical")
        if comparison_worktree(worktree_path, case_sensitive=case_sensitive) != worktree_path:
            raise PathIdentityError("claim path identity worktree_path is not canonical")
        raw_paths = data.get("paths")
        if not isinstance(raw_paths, list) or not all(
            isinstance(path, Mapping) for path in raw_paths
        ):
            raise PathIdentityError("claim path identity paths must be a list of mappings")
        paths = tuple(CanonicalPathIdentity.from_dict(path) for path in raw_paths)
        for path in paths:
            if not path.object_scope:
                continue
            _source, marker, encoded_scope = path.git_path.rpartition(_SEMANTIC_SCOPE_MARKER)
            if not marker or not encoded_scope:
                raise PathIdentityError(
                    "claim path identity object_scope requires a semantic git_path"
                )
            if (
                comparison_path(
                    path.object_scope,
                    case_sensitive=case_sensitive,
                )
                != encoded_scope
            ):
                raise PathIdentityError("claim path identity object_scope does not match git_path")
        return cls(
            version=version,
            worktree_path=worktree_path,
            worktree_object_id=_object_identity(
                data.get("worktree_object_id", ""), field="worktree_object_id"
            ),
            filesystem_namespace=_object_identity(
                data.get("filesystem_namespace", ""), field="filesystem_namespace"
            ),
            case_sensitive=case_sensitive,
            paths=paths,
        )

    def validates_display_paths(self, paths: Sequence[str]) -> bool:
        """Return whether identity rows align one-to-one with bounded displays."""
        if len(paths) != len(self.paths):
            return False
        return all(
            comparison_path(path, case_sensitive=self.case_sensitive) == identity.git_path
            for path, identity in zip(paths, self.paths, strict=True)
        )

    def validates_display_scope(self, worktree: str, paths: Sequence[str]) -> bool:
        """Return whether the identity binds both ordinary claim scope fields."""
        return comparison_worktree(
            worktree, case_sensitive=self.case_sensitive
        ) == self.worktree_path and self.validates_display_paths(paths)


def parse_optional_claim_scope_identity(
    data: Mapping[str, Any],
) -> ClaimScopeIdentity | None:
    """Parse an optional additive identity, distinguishing absent from invalid."""
    if "path_identity" not in data:
        return None
    raw = data["path_identity"]
    if not isinstance(raw, Mapping):
        raise PathIdentityError("claim path identity must be a mapping")
    return ClaimScopeIdentity.from_dict(raw)


def claim_worktrees_match(
    display_a: str,
    identity_a: ClaimScopeIdentity | None,
    display_b: str,
    identity_b: ClaimScopeIdentity | None,
) -> bool:
    """Return whether two display/identity pairs name one physical worktree."""
    if identity_a is None and identity_b is None:
        return display_a == display_b
    if identity_a is not None and identity_b is not None:
        case_sensitive = identity_a.case_sensitive and identity_b.case_sensitive
        same_path = comparison_worktree(
            identity_a.worktree_path,
            case_sensitive=case_sensitive,
        ) == comparison_worktree(
            identity_b.worktree_path,
            case_sensitive=case_sensitive,
        )
        if not same_path:
            return False
        if (
            identity_a.filesystem_namespace
            and identity_b.filesystem_namespace
            and identity_a.filesystem_namespace != identity_b.filesystem_namespace
        ):
            return False
        if identity_a.worktree_object_id and identity_b.worktree_object_id:
            return identity_a.worktree_object_id == identity_b.worktree_object_id
        return True
    known = cast(
        ClaimScopeIdentity,
        identity_a if identity_a is not None else identity_b,
    )
    legacy = display_b if identity_a is not None else display_a
    return comparison_worktree(legacy, case_sensitive=known.case_sensitive) == known.worktree_path


def claim_object_ids_comparable(
    identity_a: ClaimScopeIdentity | None,
    identity_b: ClaimScopeIdentity | None,
) -> bool:
    """Return whether two local object-id namespaces share one proven root."""
    return bool(
        identity_a is not None
        and identity_b is not None
        and identity_a.filesystem_namespace
        and identity_a.filesystem_namespace == identity_b.filesystem_namespace
        and identity_a.worktree_object_id
        and identity_a.worktree_object_id == identity_b.worktree_object_id
    )


def _canonical_pair_overlaps(
    a: CanonicalPathIdentity,
    b: CanonicalPathIdentity,
    *,
    object_identity_safe: bool,
) -> bool:
    """Return whether two canonical path identities share a filesystem object/scope."""
    if object_identity_safe and a.object_id and a.object_id == b.object_id:
        return paths_overlap(a.object_scope, b.object_scope)
    return paths_overlap(a.git_path, b.git_path) or paths_overlap(
        a.filesystem_path, b.filesystem_path
    )


def _under_case_policy(
    identity: CanonicalPathIdentity, *, case_sensitive: bool
) -> CanonicalPathIdentity:
    """Project an already-canonical row under a conservative shared case policy."""
    return CanonicalPathIdentity(
        git_path=comparison_path(identity.git_path, case_sensitive=case_sensitive),
        filesystem_path=comparison_path(
            identity.filesystem_path,
            case_sensitive=case_sensitive,
        ),
        object_id=identity.object_id,
        object_scope=comparison_path(
            identity.object_scope,
            case_sensitive=case_sensitive,
        ),
    )


def _legacy_identity(path: str, *, case_sensitive: bool) -> CanonicalPathIdentity:
    """Project a legacy display path into the comparison policy of a new peer."""
    canonical = comparison_path(path, case_sensitive=case_sensitive)
    return CanonicalPathIdentity(git_path=canonical, filesystem_path=canonical)


def claim_scopes_conflict(
    worktree_a: str,
    paths_a: Sequence[str],
    identity_a: ClaimScopeIdentity | None,
    worktree_b: str,
    paths_b: Sequence[str],
    identity_b: ClaimScopeIdentity | None,
) -> bool:
    """Return whether two display-plus-identity claim scopes contend."""
    if not claim_worktrees_match(
        worktree_a, identity_a, worktree_b, identity_b
    ) and not claim_object_ids_comparable(identity_a, identity_b):
        return False
    if not paths_a or not paths_b:
        return True
    if identity_a is None and identity_b is None:
        return any(paths_overlap(a, b) for a in paths_a for b in paths_b)
    if identity_a is None:
        identity_b = cast(ClaimScopeIdentity, identity_b)
        left = tuple(
            _legacy_identity(path, case_sensitive=identity_b.case_sensitive) for path in paths_a
        )
        right = identity_b.paths
    elif identity_b is None:
        left = identity_a.paths
        right = tuple(
            _legacy_identity(path, case_sensitive=identity_a.case_sensitive) for path in paths_b
        )
    else:
        left = identity_a.paths
        right = identity_b.paths
    case_sensitive = (
        identity_a.case_sensitive and identity_b.case_sensitive
        if identity_a is not None and identity_b is not None
        else identity_a.case_sensitive
        if identity_a is not None
        else identity_b.case_sensitive
        if identity_b is not None
        else True
    )
    left = tuple(_under_case_policy(path, case_sensitive=case_sensitive) for path in left)
    right = tuple(_under_case_policy(path, case_sensitive=case_sensitive) for path in right)
    object_identity_safe = claim_object_ids_comparable(identity_a, identity_b)
    return any(
        _canonical_pair_overlaps(a, b, object_identity_safe=object_identity_safe)
        for a in left
        for b in right
    )


def claim_scope_covers_path(
    claim_path: str,
    claim_identity: CanonicalPathIdentity | None,
    target_path: str,
    target_identity: CanonicalPathIdentity | None,
    *,
    case_sensitive: bool | None,
    object_identity_safe: bool = False,
    filesystem_identity_safe: bool = False,
) -> bool:
    """Return whether one displayed claim path directionally covers a target."""
    if claim_identity is None and target_identity is None:
        claimed = normalize_path(claim_path)
        target = normalize_path(target_path)
        return claimed == "" or claimed == target or target.startswith(claimed + "/")
    sensitive = True if case_sensitive is None else case_sensitive
    left = _under_case_policy(
        claim_identity or _legacy_identity(claim_path, case_sensitive=sensitive),
        case_sensitive=sensitive,
    )
    right = _under_case_policy(
        target_identity or _legacy_identity(target_path, case_sensitive=sensitive),
        case_sensitive=sensitive,
    )
    if object_identity_safe and left.object_id and left.object_id == right.object_id:
        return _path_covers(left.object_scope, right.object_scope)
    return _path_covers(left.git_path, right.git_path) or (
        filesystem_identity_safe and _path_covers(left.filesystem_path, right.filesystem_path)
    )


def _path_covers(scope: str, target: str) -> bool:
    """Return whether one already-canonical relative scope owns a target."""
    return scope == "" or scope == target or target.startswith(scope + "/")

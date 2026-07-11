# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — typed webhook and pull-request data contracts
"""Validate untrusted GitHub payloads into immutable application models."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast

from synapse_github_app.errors import PayloadError

_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_SHA_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise PayloadError(f"{field} must be an object")
    return cast(Mapping[str, object], value)


def _child(parent: Mapping[str, object], field: str) -> Mapping[str, object]:
    return _mapping(parent.get(field), field)


def _string(value: object, field: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise PayloadError(f"{field} must be a non-empty string of at most {max_length} characters")
    if not value.isprintable():
        raise PayloadError(f"{field} must contain printable characters only")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PayloadError(f"{field} must be a positive integer")
    return value


def _sha(value: object, field: str) -> str:
    text = _string(value, field, max_length=64)
    if _SHA_RE.fullmatch(text) is None:
        raise PayloadError(f"{field} must be a 40- or 64-character hexadecimal object id")
    return text.lower()


def _path(value: object) -> str:
    text = _string(value, "filename", max_length=1024)
    if text.startswith("/") or "\\" in text:
        raise PayloadError("filename must be a repository-relative POSIX path")
    segments = text.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise PayloadError("filename must not contain empty, dot, or parent segments")
    return text


def normalize_paths(paths: Iterable[object]) -> tuple[str, ...]:
    """Return validated, de-duplicated repository paths in stable order."""
    return tuple(sorted({_path(path) for path in paths}))


@dataclass(frozen=True)
class Repository:
    """Validated GitHub repository identity."""

    owner: str
    name: str

    def __post_init__(self) -> None:
        """Reject identities that cannot safely form a REST path."""
        if _OWNER_RE.fullmatch(self.owner) is None:
            raise PayloadError("repository owner is invalid")
        if _REPOSITORY_RE.fullmatch(self.name) is None or self.name in {".", ".."}:
            raise PayloadError("repository name is invalid")

    @property
    def full_name(self) -> str:
        """Return the conventional owner/name identity."""
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class PullRequestSeed:
    """Open pull-request identity before changed files are attached."""

    number: int
    head_sha: str
    head_ref: str
    base_ref: str

    def __post_init__(self) -> None:
        """Keep directly constructed seeds within the webhook contract."""
        _positive_int(self.number, "pull_request.number")
        object.__setattr__(self, "head_sha", _sha(self.head_sha, "pull_request.head.sha"))
        _string(self.head_ref, "pull_request.head.ref", max_length=512)
        _string(self.base_ref, "pull_request.base.ref", max_length=512)

    @classmethod
    def from_api(cls, value: object) -> PullRequestSeed:
        """Parse one pull-request object returned by GitHub REST."""
        pull = _mapping(value, "pull request")
        head = _child(pull, "head")
        base = _child(pull, "base")
        return cls(
            number=_positive_int(pull.get("number"), "pull_request.number"),
            head_sha=_sha(head.get("sha"), "pull_request.head.sha"),
            head_ref=_string(head.get("ref"), "pull_request.head.ref", max_length=512),
            base_ref=_string(base.get("ref"), "pull_request.base.ref", max_length=512),
        )

    def with_paths(
        self, paths: Iterable[object], *, paths_truncated: bool = False
    ) -> PullRequestSnapshot:
        """Attach validated changed paths to this pull request."""
        return PullRequestSnapshot(
            number=self.number,
            head_sha=self.head_sha,
            head_ref=self.head_ref,
            base_ref=self.base_ref,
            paths=normalize_paths(paths),
            paths_truncated=paths_truncated,
        )


@dataclass(frozen=True)
class PullRequestSnapshot:
    """Pull-request identity plus its bounded changed-file set."""

    number: int
    head_sha: str
    head_ref: str
    base_ref: str
    paths: tuple[str, ...]
    paths_truncated: bool = False

    def __post_init__(self) -> None:
        """Validate direct construction and preserve deterministic path order."""
        _positive_int(self.number, "pull_request.number")
        object.__setattr__(self, "head_sha", _sha(self.head_sha, "pull_request.head.sha"))
        _string(self.head_ref, "pull_request.head.ref", max_length=512)
        _string(self.base_ref, "pull_request.base.ref", max_length=512)
        if not isinstance(self.paths_truncated, bool):
            raise PayloadError("paths_truncated must be boolean")
        normalized = normalize_paths(self.paths)
        object.__setattr__(self, "paths", normalized)

    @property
    def branch_key(self) -> str:
        """Return a PR-unique branch key for the core conflict finder."""
        return f"pull/{self.number}"


@dataclass(frozen=True)
class PullRequestEvent:
    """Authenticated pull-request webhook fields required by the service."""

    action: str
    delivery_id: str
    installation_id: int
    repository: Repository
    pull_request: PullRequestSeed

    @classmethod
    def from_payload(cls, value: object, *, delivery_id: str) -> PullRequestEvent:
        """Parse one authenticated GitHub pull-request webhook payload."""
        payload = _mapping(value, "payload")
        repository = _child(payload, "repository")
        owner = _child(repository, "owner")
        installation = _child(payload, "installation")
        return cls(
            action=_string(payload.get("action"), "action", max_length=64),
            delivery_id=_string(delivery_id, "X-GitHub-Delivery", max_length=128),
            installation_id=_positive_int(installation.get("id"), "installation.id"),
            repository=Repository(
                owner=_string(owner.get("login"), "repository.owner.login", max_length=39),
                name=_string(repository.get("name"), "repository.name", max_length=100),
            ),
            pull_request=PullRequestSeed.from_api(payload.get("pull_request")),
        )

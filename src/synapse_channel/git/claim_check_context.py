# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — staged claim-check identity and Git context
"""Resolve one exact repository, branch, identity, and hub configuration."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.core.errors import SynapseError
from synapse_channel.git.gitclaim import GitError, GitRunner, _default_git_runner

_PLACEHOLDERS = frozenset({"ME", "USER", "YOUR_IDENTITY"})


class ClaimCheckConfigError(SynapseError, RuntimeError):
    """The staged claim check cannot bind one trustworthy local identity."""

    code = "claim_check_config"


@dataclass(frozen=True)
class ClaimCheckContext:
    """Canonical local inputs for one read-only staged claim decision."""

    root: Path
    branch: str
    identity: str
    uri: str
    token_file: Path | None
    requester: str


def _config_value(key: str, *, runner: GitRunner) -> str:
    return runner(["config", "--local", "--get", "--default", "", key]).strip()


def _session_identity(environment: Mapping[str, str]) -> str:
    project = environment.get("SYN_PROJECT", "").strip()
    identity = environment.get("SYN_IDENTITY", "").strip()
    if bool(project) != bool(identity):
        raise ClaimCheckConfigError(
            "SYN_PROJECT and SYN_IDENTITY must be supplied together for claim enforcement."
        )
    if identity and identity != project and not identity.startswith(project + "/"):
        raise ClaimCheckConfigError("SYN_IDENTITY does not belong to SYN_PROJECT.")
    return identity


def _resolve_identity(
    *, explicit: str | None, configured: str, environment: Mapping[str, str]
) -> str:
    candidates = (explicit or "", configured, _session_identity(environment))
    sources = [value.strip() for value in candidates if value.strip()]
    if not sources:
        raise ClaimCheckConfigError(
            "No claim identity is configured; run `synapse git-init --name <exact-owner>`."
        )
    if len(set(sources)) != 1:
        raise ClaimCheckConfigError("Claim identity sources disagree; refresh repository config.")
    identity = sources[0]
    if any(segment in _PLACEHOLDERS for segment in identity.split("/")):
        raise ClaimCheckConfigError("Placeholder identities cannot enforce staged claims.")
    return identity


def _resolve_token_file(raw: str, *, root: Path) -> Path | None:
    if not raw.strip():
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ClaimCheckConfigError("The configured Synapse token-file path is invalid.") from exc


def resolve_claim_check_context(
    *,
    identity: str | None = None,
    uri: str | None = None,
    token_file: str | None = None,
    runner: GitRunner = _default_git_runner,
    environment: Mapping[str, str] | None = None,
) -> ClaimCheckContext:
    """Resolve canonical Git context and require all populated identities to agree."""
    env = os.environ if environment is None else environment
    root_text = runner(["rev-parse", "--show-toplevel"]).strip()
    if not root_text:
        raise ClaimCheckConfigError("Git returned no repository root.")
    try:
        root = Path(root_text).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ClaimCheckConfigError("Git returned an invalid repository root.") from exc
    try:
        branch = runner(["symbolic-ref", "--quiet", "--short", "HEAD"]).strip()
    except GitError as exc:
        raise ClaimCheckConfigError("Detached HEAD cannot satisfy branch-scoped claims.") from exc
    if not branch:
        raise ClaimCheckConfigError("Git returned no current branch.")

    selected_identity = _resolve_identity(
        explicit=identity,
        configured=_config_value("synapse.identity", runner=runner),
        environment=env,
    )
    selected_uri = (
        (uri or "").strip()
        or _config_value("synapse.uri", runner=runner)
        or env.get("SYNAPSE_URI", "").strip()
        or DEFAULT_HUB_URI
    )
    selected_token_file = _resolve_token_file(
        token_file if token_file is not None else _config_value("synapse.tokenFile", runner=runner),
        root=root,
    )
    digest = hashlib.sha256(selected_identity.encode("utf-8")).hexdigest()[:16]
    return ClaimCheckContext(
        root=root,
        branch=branch,
        identity=selected_identity,
        uri=selected_uri,
        token_file=selected_token_file,
        requester=f"claim-check/{digest}",
    )

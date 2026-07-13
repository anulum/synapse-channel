# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — worktree-scoped staged-claim configuration
"""Persist and read staged-claim configuration without cross-worktree bleed.

Git's ordinary local configuration lives in the common repository directory, so
every linked worktree sees and mutates the same values.  The staged-claim gate
needs the opposite: one exact identity, hub, and optional token-file path per
worktree.  This module owns the official ``extensions.worktreeConfig`` setup and
keeps that policy out of the onboarding and decision modules.
"""

from __future__ import annotations

from pathlib import Path

from synapse_channel.git.gitclaim import GitError, GitRunner
from synapse_channel.path_resolution import resolve_weakly_fail_closed

_WORKTREE_CONFIG_EXTENSION = "extensions.worktreeConfig"
_CLAIM_CHECK_KEYS = ("synapse.identity", "synapse.uri", "synapse.tokenFile")


def _scoped_value(scope: str, key: str, *, runner: GitRunner) -> str:
    """Read one value from exactly the requested repository config scope."""
    return runner(["config", scope, "--get", "--default", "", key]).strip()


def _scoped_bool(scope: str, key: str, *, runner: GitRunner) -> bool:
    """Read one Git boolean in canonical form from exactly one config scope."""
    value = runner(["config", scope, "--type=bool", "--get", "--default", "false", key]).strip()
    return value == "true"


def _worktree_config_enabled(*, runner: GitRunner) -> bool:
    """Return whether Git's per-worktree configuration extension is enabled."""
    return _scoped_bool("--local", _WORKTREE_CONFIG_EXTENSION, runner=runner)


def _enable_worktree_config(*, runner: GitRunner) -> None:
    """Enable per-worktree config, refusing Git layouts that require migration.

    Git documents ``core.worktree`` and ``core.bare=true`` as values that must be
    moved before enabling ``extensions.worktreeConfig``.  Guessing that migration
    could make the repository unusable, so onboarding fails closed with a focused
    repair instead.
    """
    if _worktree_config_enabled(runner=runner):
        return
    core_worktree = _scoped_value("--local", "core.worktree", runner=runner)
    core_bare = _scoped_bool("--local", "core.bare", runner=runner)
    if core_worktree or core_bare:
        raise GitError(
            "cannot safely enable per-worktree claim configuration while the shared "
            "Git config contains core.worktree or core.bare=true; move that value to "
            "the main worktree's config.worktree as documented by git-worktree, then "
            "rerun `synapse git-init`"
        )
    runner(["config", "--local", _WORKTREE_CONFIG_EXTENSION, "true"])


def _unset_if_present(scope: str, key: str, *, runner: GitRunner) -> None:
    """Remove one scoped key only when present, avoiding Git's non-zero no-op."""
    if _scoped_value(scope, key, runner=runner):
        runner(["config", scope, "--unset-all", key])


def read_claim_check_config(key: str, *, runner: GitRunner) -> str:
    """Read a staged-gate value from the current worktree or legacy local config.

    Once per-worktree config is enabled, the common repository value is never a
    fallback: inheriting another seat's identity would turn a missing setup into
    an authorization ambiguity.  Repositories not yet migrated retain the legacy
    local read until ``synapse git-init`` is rerun.
    """
    if _worktree_config_enabled(runner=runner):
        return _scoped_value("--worktree", key, runner=runner)
    return _scoped_value("--local", key, runner=runner)


def persist_claim_check_config(
    *, uri: str, name: str, token_file: str | None, runner: GitRunner
) -> tuple[list[str], str | None]:
    """Write non-secret staged-gate inputs into the current worktree config."""
    canonical_token: str | None = None
    if token_file:
        try:
            canonical_token = str(resolve_weakly_fail_closed(Path(token_file).expanduser()))
        except (OSError, RuntimeError) as exc:
            raise GitError("the Synapse token-file path is invalid") from exc

    _enable_worktree_config(runner=runner)
    runner(["config", "--worktree", "synapse.identity", name])
    runner(["config", "--worktree", "synapse.uri", uri])
    if canonical_token:
        runner(["config", "--worktree", "synapse.tokenFile", canonical_token])
    else:
        _unset_if_present("--worktree", "synapse.tokenFile", runner=runner)

    # Old releases wrote these values into the shared repository config.  Clear
    # them only after the current worktree has its replacement so another linked
    # worktree fails closed instead of silently inheriting this seat's identity.
    for key in _CLAIM_CHECK_KEYS:
        _unset_if_present("--local", key, runner=runner)

    results = [
        "recorded worktree-specific synapse.identity and synapse.uri for staged claim checks"
    ]
    if canonical_token:
        results.append(
            "recorded worktree-specific synapse.tokenFile path (token content was not stored)"
        )
    else:
        results.append("cleared worktree-specific synapse.tokenFile (no token file requested)")
    return results, canonical_token

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — resolve the auto-release identity for the current worktree
"""Resolve which seat a shared auto-release hook should act as, per worktree.

Git worktrees share one hooks directory, so the single ``post-commit`` /
``post-merge`` hook that :mod:`synapse_channel.git.githook` installs fires in
whichever linked worktree just committed. A hook that baked one identity at
install time therefore released the *installer's* claims from every worktree —
mixed-identity worktrees had to fall back to ``--auto-release-on manual``.

This module owns the small, distinct responsibility of reading the per-worktree
``synapse.identity`` / ``synapse.uri`` / ``synapse.tokenFile`` that
``synapse git-init`` records (via :mod:`synapse_channel.git.claim_check_config`)
for exactly the current worktree, so one shared hook can act as the seat that
owns the commit. It fails closed: with no configured identity — or only a
placeholder — it returns ``None`` so the hook releases nothing rather than
dropping another seat's claims under a stale name. A hook must never block a
commit, so every failure mode here resolves to ``None`` or a tokenless identity;
nothing is raised.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.git.claim_check_config import read_claim_check_config
from synapse_channel.git.gitclaim import GitError, GitRunner, _default_git_runner

#: Onboarding placeholders that never identify a real seat. Mirrors the staged
#: claim check's guard so a default ``git-init --name USER`` cannot make a shared
#: hook release claims as the literal placeholder.
_PLACEHOLDER_IDENTITIES = frozenset({"ME", "USER", "YOUR_IDENTITY"})


@dataclass(frozen=True)
class ReleaseIdentity:
    """The hub seat a shared auto-release hook should act as for one worktree.

    Attributes
    ----------
    uri : str
        Hub URI the release connects to.
    name : str
        Exact seat identity whose branch-scoped claims are released.
    token : str or None
        Shared-secret token for a secured hub, read from the worktree's
        configured token file; ``None`` when no usable token file is configured.
    """

    uri: str
    name: str
    token: str | None


def _read_token(token_file: str) -> str | None:
    """Read a hub token from a worktree-configured file, or ``None`` when unusable.

    A blank configuration, a missing file, or an unreadable one all yield
    ``None`` rather than raising: the auto-release hook must not block a commit,
    and a tokenless release simply cannot authenticate to a secured hub (which
    :func:`synapse_channel.git.githook.run_git_release` already treats as a
    no-op, not an error).
    """
    if not token_file.strip():
        return None
    try:
        content = Path(token_file).expanduser().read_text(encoding="utf-8")
    except OSError:
        return None
    return content.strip() or None


def resolve_release_identity(*, runner: GitRunner = _default_git_runner) -> ReleaseIdentity | None:
    """Resolve the current worktree's auto-release seat, or ``None`` to release nothing.

    Reads the per-worktree ``synapse.identity`` / ``synapse.uri`` /
    ``synapse.tokenFile`` recorded by ``synapse git-init`` so a repository-wide
    hook acts as the seat that owns the commit in *this* worktree, not the seat
    that last ran the installer.

    Parameters
    ----------
    runner : GitRunner, optional
        The git executor; injectable for testing.

    Returns
    -------
    ReleaseIdentity or None
        The resolved seat, or ``None`` when the worktree has no configured
        identity or only a placeholder — so the hook fails closed instead of
        releasing another seat's claims. A git failure while reading the config
        also resolves to ``None``; a hook must never block a commit.
    """
    try:
        name = read_claim_check_config("synapse.identity", runner=runner).strip()
    except GitError:
        return None
    if not name or any(segment in _PLACEHOLDER_IDENTITIES for segment in name.split("/")):
        return None
    try:
        uri = read_claim_check_config("synapse.uri", runner=runner).strip() or DEFAULT_HUB_URI
        token_file = read_claim_check_config("synapse.tokenFile", runner=runner)
    except GitError:
        return None
    return ReleaseIdentity(uri=uri, name=name, token=_read_token(token_file))

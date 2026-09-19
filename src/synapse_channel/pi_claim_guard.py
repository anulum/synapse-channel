# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pi tool mutation claim boundary
"""Bind pi file writes to one project, worktree, task, epoch and session."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.claim_state import ClaimStateError, fetch_state_snapshot
from synapse_channel.file_claim_guard import (
    GuardVerdict,
    MutationRequest,
    StateFetcher,
    evaluate_mutation_request,
)
from synapse_channel.git.claim_coverage import EDITABLE_STATUSES
from synapse_channel.git.gitclaim import GitRunner, _default_git_runner
from synapse_channel.path_resolution import resolve_weakly_fail_closed

MAX_PI_HOOK_BYTES = 65_536
_MUTATION_TOOLS = frozenset({"write", "edit"})
_PI_UNICODE_SPACES = frozenset(
    "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"
)


@dataclass(frozen=True)
class PiGuardContext:
    """Authoritative claim identity fixed when one pi process is launched."""

    identity: str
    project: str
    repository: Path
    task_id: str
    epoch: int
    session_id: str

    def validate(self) -> None:
        """Reject ambiguous or unbound process-side claim context."""
        if not self.project or not self.identity.startswith(self.project + "/"):
            raise ValueError("pi identity is outside its configured project")
        if not self.task_id or not self.session_id or type(self.epoch) is not int:
            raise ValueError("pi task, epoch and session must be explicit")
        if self.epoch <= 0 or not self.repository.is_absolute():
            raise ValueError("pi claim epoch and repository must be valid")


def _parse_event(raw: str, context: PiGuardContext) -> MutationRequest:
    """Parse one bounded native pi tool_call request without path guessing."""
    if len(raw.encode("utf-8")) > MAX_PI_HOOK_BYTES:
        raise ValueError("pi hook event exceeds its byte limit")
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("pi hook event must be JSON") from exc
    if not isinstance(event, dict) or event.get("event") != "tool_call":
        raise ValueError("pi hook requires a tool_call event")
    tool = event.get("tool_name")
    if tool == "bash":
        raise ValueError("pi shell effects require an independently enforced sandbox")
    if tool not in _MUTATION_TOOLS:
        raise ValueError("pi hook accepts only write or edit mutations")
    if event.get("session_id") != context.session_id:
        raise ValueError("pi hook session does not match the launched session")
    cwd = event.get("cwd")
    path = event.get("input")
    call_id = event.get("tool_call_id")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("pi hook cwd must be absolute")
    if not isinstance(path, dict) or not isinstance(path.get("path"), str):
        raise ValueError("pi hook write/edit input needs an exact path")
    if not path["path"] or path["path"] != path["path"].strip():
        raise ValueError("pi hook target path must be non-empty and exact")
    target = path["path"]
    # Pi's resolveToCwd rewrites these spellings before write/edit executes.
    # A literal Path check would authorize a different file than Pi writes.
    if target.startswith(("~", "@", "file://")) or any(
        character in _PI_UNICODE_SPACES for character in target
    ):
        raise ValueError("pi hook target path uses a rewritten Pi spelling")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("pi hook tool call ID is required")
    return MutationRequest(
        session_id=context.session_id,
        tool_use_id=call_id,
        cwd=Path(cwd),
        file_paths=(Path(target),),
        allow_semantic_source=tool == "edit",
    )


def claim_epoch_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    identity: str,
    project: str,
    repository: Path,
    task_id: str,
) -> int | None:
    """Read one editable exact-task epoch without widening claim authority."""
    if not project or not identity.startswith(project + "/") or not task_id:
        return None
    claims = snapshot.get("active_claims")
    if not isinstance(claims, list):
        return None
    try:
        root = resolve_weakly_fail_closed(repository)
    except (OSError, RuntimeError, ValueError):
        return None
    matches: list[int] = []
    for claim in claims:
        if not isinstance(claim, dict):
            return None
        if claim.get("task_id") != task_id:
            continue
        if claim.get("owner") != identity:
            return None
        worktree = claim.get("worktree")
        if not isinstance(worktree, str):
            return None
        try:
            claimed_root = resolve_weakly_fail_closed(Path(worktree))
        except (OSError, RuntimeError, ValueError):
            return None
        if claimed_root != root:
            return None
        if claim.get("status") not in EDITABLE_STATUSES:
            return None
        epoch = claim.get("epoch")
        if type(epoch) is not int or epoch <= 0:
            return None
        matches.append(epoch)
    return matches[0] if len(matches) == 1 else None


def _claim_matches(snapshot: Mapping[str, Any], context: PiGuardContext) -> bool:
    """Require exactly one editable claim at the configured epoch and root."""
    return (
        claim_epoch_from_snapshot(
            snapshot,
            identity=context.identity,
            project=context.project,
            repository=context.repository,
            task_id=context.task_id,
        )
        == context.epoch
    )


async def evaluate_pi_hook(
    raw: str,
    *,
    context: PiGuardContext,
    uri: str,
    token: str | None,
    timeout: float,
    state_fetcher: StateFetcher = fetch_state_snapshot,
    git_runner: GitRunner = _default_git_runner,
) -> GuardVerdict:
    """Check one pi write/edit against fresh hub state and file claim coverage."""
    try:
        context.validate()
        request = _parse_event(raw, context)
        cwd = resolve_weakly_fail_closed(request.cwd)
        root = resolve_weakly_fail_closed(context.repository)
        cwd.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        return GuardVerdict(False, str(exc))

    async def bound_fetcher(**kwargs: Any) -> dict[str, Any]:
        """Reject stale or substituted claims before normal file coverage logic."""
        snapshot = await state_fetcher(**kwargs)
        if not _claim_matches(snapshot, context):
            raise ClaimStateError("pi claim task, owner, worktree or epoch no longer matches")
        return snapshot

    return await evaluate_mutation_request(
        request,
        provider="pi",
        identity=context.identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=bound_fetcher,
        git_runner=git_runner,
    )

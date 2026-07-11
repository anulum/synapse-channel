# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only staged path claim enforcement
"""Evaluate staged Git paths against one authoritative Synapse snapshot."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.claim_state import ClaimStateError, fetch_state_snapshot
from synapse_channel.git.claim_check_context import (
    ClaimCheckConfigError,
    resolve_claim_check_context,
)
from synapse_channel.git.claim_coverage import ClaimCoverageError, decide_claim_coverage
from synapse_channel.git.gitclaim import GitError, GitRunner, _default_git_runner
from synapse_channel.git.staged_paths import read_staged_paths

StateFetcher = Callable[..., Awaitable[dict[str, Any]]]
MAX_DIAGNOSTIC_PATHS = 32
MAX_DIAGNOSTIC_CHARS = 2048


@dataclass(frozen=True)
class StagedClaimCheckResult:
    """One commit-gate decision and its bounded operator diagnostic."""

    allowed: bool
    paths: tuple[str, ...]
    reason: str = ""


def _bounded_paths(paths: Sequence[str]) -> str:
    rendered: list[str] = []
    used = 0
    for path in paths[:MAX_DIAGNOSTIC_PATHS]:
        item = json.dumps(path, ensure_ascii=True)
        added = len(item) + (2 if rendered else 0)
        if used + added > MAX_DIAGNOSTIC_CHARS:
            break
        rendered.append(item)
        used += added
    omitted = len(paths) - len(rendered)
    separator = " " if rendered else ""
    suffix = f"{separator}(+{omitted} more)" if omitted else ""
    return ", ".join(rendered) + suffix


def _coverage_reason(
    missing: Sequence[str], ownership: Sequence[str], non_editable: Sequence[str]
) -> str:
    groups: list[str] = []
    if missing:
        groups.append(f"no covering claim: {_bounded_paths(missing)}")
    if ownership:
        groups.append(f"owned by another or mixed identity: {_bounded_paths(ownership)}")
    if non_editable:
        groups.append(f"covering claim is not editable: {_bounded_paths(non_editable)}")
    return "; ".join(groups)


def _token(environment: Mapping[str, str], token_file: Path | None) -> str | None:
    if token_file is None:
        return environment.get("SYNAPSE_TOKEN", "").strip() or None
    value = token_file.read_text(encoding="utf-8").strip()
    if not value:
        raise ClaimCheckConfigError("The configured Synapse token file is empty.")
    return value


async def run_staged_claim_check(
    *,
    identity: str | None = None,
    uri: str | None = None,
    token_file: str | None = None,
    timeout: float = 5.0,
    runner: GitRunner = _default_git_runner,
    environment: Mapping[str, str] | None = None,
    state_fetcher: StateFetcher = fetch_state_snapshot,
) -> StagedClaimCheckResult:
    """Allow only when one exact owner covers every path in the staged index."""
    env = os.environ if environment is None else environment
    paths: tuple[str, ...] = ()
    try:
        paths = read_staged_paths(runner=runner)
        if not paths:
            return StagedClaimCheckResult(True, ())
        context = resolve_claim_check_context(
            identity=identity,
            uri=uri,
            token_file=token_file,
            runner=runner,
            environment=env,
        )
        snapshot = await state_fetcher(
            uri=context.uri,
            requester=context.requester,
            token=_token(env, context.token_file),
            timeout=timeout,
        )
        verdict = decide_claim_coverage(
            snapshot,
            identity=context.identity,
            root=context.root,
            branch=context.branch,
            paths=paths,
        )
    except (ClaimCheckConfigError, ClaimCoverageError, ClaimStateError, GitError, OSError) as exc:
        return StagedClaimCheckResult(False, paths, str(exc))
    if verdict.allowed:
        return StagedClaimCheckResult(True, paths)
    return StagedClaimCheckResult(
        False,
        paths,
        _coverage_reason(
            verdict.missing_paths,
            verdict.ownership_mismatch_paths,
            verdict.non_editable_paths,
        ),
    )

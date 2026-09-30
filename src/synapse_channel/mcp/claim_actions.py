# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP claim and receipt-bearing release actions
"""Translate MCP claim/release calls into correlated hub operations."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.claim_confirmation import (
    DEFAULT_CLAIM_REPLY_TIMEOUT,
    ClaimIntent,
    confirm_claim,
    valid_claim_timeout,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.ordinary_claim import (
    OrdinaryClaimScopeError,
    resolve_ordinary_claim_scope,
)
from synapse_channel.mcp.git_claim import McpGitClaimError, resolve_mcp_git_claim_scope

Matcher = Callable[[dict[str, Any]], bool]
Sender = Callable[[], Awaitable[None]]
ReplyAwaiter = Callable[[Matcher, Sender], Awaitable[dict[str, Any] | None]]
TimedReplyAwaiter = Callable[[Matcher, Sender, float], Awaitable[dict[str, Any] | None]]


class McpClaimActions:
    """Own MCP task/Git claims and receipt-validated releases.

    Parameters
    ----------
    name : str
        Exact bridge identity expected in grants and receipts.
    agent : SynapseAgent
        Connected hub client used to issue claim and release operations.
    await_reply : ReplyAwaiter
        Correlator owned by the bridge transport layer.
    await_timed_reply : TimedReplyAwaiter
        Correlator with a per-exchange deadline for Git claims and confirmation.
    """

    def __init__(
        self,
        name: str,
        agent: SynapseAgent,
        await_reply: ReplyAwaiter,
        *,
        await_timed_reply: TimedReplyAwaiter,
    ) -> None:
        self.name = name
        self.agent = agent
        self.await_reply = await_reply
        self.await_timed_reply = await_timed_reply

    async def claim(
        self, task_id: str, paths: list[str] | None = None, *, task_only: bool = False
    ) -> str:
        """Claim a task lease: file paths, the whole current worktree, or the task alone.

        Parameters
        ----------
        task_id : str
            The task to lease.
        paths : list[str] or None, optional
            Repository-relative paths the claim covers. Without paths, a claim made
            inside a Git worktree covers that whole worktree; outside one it is
            refused unless ``task_only`` is set.
        task_only : bool, optional
            Lease the task id alone with no file scope, keyed by the task id as
            ``synapse lock`` does; it contends only with the same task. Cannot be
            combined with ``paths``.

        Returns
        -------
        str
            The grant or refusal, naming the scope actually sent to the hub.
        """
        scope = list(paths or [])
        if task_only:
            if scope:
                return "claim refused: task_only cannot be combined with paths"
            return await self._claim(
                task_id,
                paths=[],
                worktree=task_id,
                path_identity=None,
                git=None,
                where="no file scope, task-only lock",
            )
        worktree = ""
        path_identity: dict[str, object] | None = None
        try:
            resolved_scope = resolve_ordinary_claim_scope(scope, whole_worktree=not scope)
        except OrdinaryClaimScopeError as exc:
            return f"claim refused: {exc}"
        if resolved_scope is not None:
            scope = list(resolved_scope.paths)
            worktree = resolved_scope.worktree
            path_identity = resolved_scope.path_identity
        elif not scope:
            return (
                "claim refused: outside a Git worktree a claim needs paths, or "
                "task_only=true for a task lock with no file scope"
            )
        where = ", ".join(scope) if scope else f"the whole worktree {worktree}"
        return await self._claim(
            task_id,
            paths=scope,
            worktree=worktree,
            path_identity=path_identity,
            git=None,
            where=where,
        )

    async def git_claim(
        self,
        task_id: str,
        paths: Sequence[str] | None = None,
        *,
        base: str = "main",
        auto_release_on: str = "manual",
        whole_worktree: bool = False,
        reply_timeout: float = DEFAULT_CLAIM_REPLY_TIMEOUT,
        confirm_only: bool = False,
    ) -> str:
        """Claim or confirm an exact Git scope through the MCP face.

        Parameters
        ----------
        task_id : str
            Task whose lease is requested or confirmed.
        paths : Sequence[str] or None
            Canonical repository-relative file scopes.
        base : str
            Intended integration branch stored on the lease.
        auto_release_on : str
            Client-side release policy: manual, commit or merge.
        whole_worktree : bool
            Explicitly request the whole worktree instead of bounded paths.
        reply_timeout : float
            Seconds per send/reply exchange; finite, positive and at most 300.
        confirm_only : bool
            Verify the existing exact live lease without claiming or renewing it.

        Returns
        -------
        str
            Grant, confirmation, refusal or unknown outcome. Unknown never
            authorizes edits; confirmation requires an exact scope and live fence.
        """
        if not valid_claim_timeout(reply_timeout):
            return "git claim refused: deadline must be finite, positive and at most 300 seconds"
        try:
            scope = resolve_mcp_git_claim_scope(
                paths,
                base=base,
                auto_release_on=auto_release_on,
                whole_worktree=whole_worktree,
            )
        except McpGitClaimError as exc:
            return f"git claim refused: {exc}"
        where = (
            f"{', '.join(scope.paths) if scope.paths else 'the whole worktree'} "
            f"on branch {scope.git['branch']}"
        )
        return await self._claim(
            task_id,
            paths=list(scope.paths),
            worktree=scope.worktree,
            path_identity=scope.path_identity,
            git=scope.git,
            where=where,
            reply_timeout=reply_timeout,
            confirm_only=confirm_only,
        )

    async def _claim(
        self,
        task_id: str,
        *,
        paths: list[str],
        worktree: str,
        path_identity: dict[str, object] | None,
        git: dict[str, str] | None,
        where: str,
        reply_timeout: float = DEFAULT_CLAIM_REPLY_TIMEOUT,
        confirm_only: bool = False,
    ) -> str:
        """Issue one claim, or prove an uncertain Git lease through fresh state."""
        task_id = task_id.strip()
        intent = ClaimIntent(task_id, self.name, worktree, tuple(paths), path_identity, git)

        async def await_claim_reply(match: Matcher, send: Sender) -> dict[str, Any] | None:
            """Use the timed correlator when the owning bridge supplies it."""
            return await self.await_timed_reply(match, send, reply_timeout)

        def match(data: dict[str, Any]) -> bool:
            if data.get("task_id") != task_id:
                return False
            kind = data.get("type")
            if kind == MessageType.CLAIM_GRANTED:
                return (
                    intent.matches(data, now=time.time())
                    if git is not None
                    else data.get("owner") == self.name
                )
            return kind == MessageType.CLAIM_DENIED

        reply = (
            None
            if confirm_only
            else await (await_claim_reply if git is not None else self.await_reply)(
                match,
                lambda: self.agent.claim(
                    task_id,
                    worktree=worktree,
                    paths=paths,
                    path_identity=path_identity,
                    git=git,
                ),
            )
        )
        if reply is None:
            if git is not None:
                if await confirm_claim(self.agent, await_claim_reply, intent):
                    return f"claim confirmed: '{task_id}' ({where}; live lease, no mutation replay)"
                return (
                    f"claim outcome unknown: '{task_id}'; no confirmed live lease. "
                    "Use confirm_only=true with the same identity and scope before working."
                )
            return f"claim '{task_id}': no response from the hub"
        if reply.get("type") == MessageType.CLAIM_GRANTED:
            return f"claim granted: '{task_id}' ({where})"
        return f"claim denied: '{task_id}' — {reply.get('payload') or 'held by another agent'}"

    async def release(
        self,
        task_id: str,
        *,
        evidence: Sequence[str] = (),
        changed_files: Sequence[str] = (),
        confidence: str = "",
    ) -> str:
        """Release a held lease only when the hub returns a matching receipt."""

        def match(data: dict[str, Any]) -> bool:
            return data.get("task_id") == task_id and data.get("type") in {
                MessageType.RELEASE_GRANTED,
                MessageType.RELEASE_DENIED,
            }

        reply = await self.await_reply(
            match,
            lambda: self.agent.release(
                task_id,
                evidence=list(evidence),
                changed_files=list(changed_files),
                confidence=confidence,
            ),
        )
        if reply is None:
            return f"release '{task_id}': no response from the hub"
        if reply.get("type") == MessageType.RELEASE_GRANTED:
            receipt = reply.get("receipt")
            if not isinstance(receipt, Mapping):
                return f"released '{task_id}', but the hub returned no valid receipt"
            if (
                receipt.get("task_id") != task_id
                or receipt.get("owner") != self.name
                or receipt.get("released") is not True
            ):
                return f"released '{task_id}', but the hub returned a mismatched receipt"
            return f"released '{task_id}' with receipt owner '{self.name}'"
        return f"release denied: '{task_id}' — {reply.get('payload') or 'not the owner'}"

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stop a scope and seal each attempt the stop listed
"""``synapse operation stop --seal``: record the stop, then seal each listed attempt.

On one connection, as the stopper's identity, the command:

1. records the stop, receives its stored answer when the request key is
   repeated, or adopts the standing stop's ``standing_seq`` and still-open
   ``listed`` attempts when the scope is ``already_stopped``, and stops on any
   other answer;
2. for each attempt to seal, reads the operation's declaration from the hub
   (``operation_state``, which needs ``operation-evidence`` or
   ``operation-seal`` on the task);
3. for an operation whose effect has no marker at a system of record, records
   ``unsealable``; otherwise creates the attempt's ``sealed`` marker in
   ``--repository`` (or pushes it to ``--remote``) and reads it back, exactly
   as ``synapse operation seal`` does;
4. records what it read back: ``sealed`` (after the ``seal`` record, joined to
   the stop by its sequence, when the attempt was still open; a concurrent
   ``attempt_ended`` refusal on the ``seal`` record is non-fatal and proceeds
   to the read-back), ``applied`` (the effect landed first) or ``unread`` (the
   seal did not reach the repository, or its state cannot be told).

Each attempt is sealed and read back before anything is recorded for it, and
the request key is printed on every exit path once the hub link is entered. An
attempt whose task the stopper holds no ``operation-seal`` on is reported
``not_permitted`` and left for an identity that holds it.

Exit codes: 0 every listed attempt is sealed or applied; 1 the scope was not
stopped, or an attempt is not closed; 2 no verdict (the hub is unreachable or
silent); 4 the hub does not know the request; 5 no ``git``.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.cli_operation_seal import (
    EXIT_DONE,
    EXIT_NO_VERDICT,
    EXIT_REFUSED,
    EXIT_UNUSABLE,
    _attempt_state,
    _hub_refusal,
    _HubLink,
)
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.attempt_markers import (
    LANDED,
    LANDED_EARLIER,
    OTHER_PARTY_FIRST,
    UNKNOWN,
    AttemptMarker,
    AttemptMarkerError,
)
from synapse_channel.core.operation_seals import REFUSED_ATTEMPT_ENDED
from synapse_channel.core.operation_stop_readbacks import (
    CLOSING_RESULTS,
    REFUSED_READBACK_CLOSED,
    RESULT_APPLIED,
    RESULT_SEALED,
    RESULT_UNREAD,
    RESULT_UNSEALABLE,
    UNREAD_REASONS,
)
from synapse_channel.core.operation_stops import ALREADY_STOPPED, STOP_RECORDED
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.attempt_marker_changes import Target, object_format, seal
from synapse_channel.git.git_runtime import GitError, git_executable
from synapse_channel.terminal_text import terminal_text

NOT_PERMITTED = "not_permitted"
REFUSED = "refused"
RECORDED_BEFORE = "recorded_before"
"""Outcomes of one listed attempt that are not a read-back result."""

_GIT_EFFECT = "git_publication"
_SEALABLE_STOP_ANSWERS = (STOP_RECORDED, ALREADY_STOPPED)


async def _stop(link: _HubLink, args: argparse.Namespace) -> dict[str, Any] | int:
    """Record the stop (or adopt the standing one) and return its answer, or an exit code."""
    key = str(args.idem_key)
    reply = await link.ask(
        MessageType.OPERATION_STOP,
        MessageType.OPERATION_STOP_RESULT,
        key=key,
        scope=args.scope,
        reason=args.reason,
        idem_key=key,
    )
    if reply is None or reply.get("type") == MessageType.ERROR:
        refused = _hub_refusal(reply, "operation stop")
        return EXIT_REFUSED if refused is None else refused
    answer = str(reply.get("answer") or "")
    print(
        f"operation stop {answer} for {terminal_text(args.scope)}: "
        f"{terminal_text(str(reply.get('payload') or ''))}"
    )
    if answer not in _SEALABLE_STOP_ANSWERS:
        return EXIT_REFUSED
    return reply


async def _record(
    link: _HubLink, item: dict[str, str], record: str, body: dict[str, Any]
) -> dict[str, Any] | None:
    """Send one ``seal`` or ``stop_readback`` record and return the hub's answer."""
    ask = (MessageType.OPERATION_RECORD, MessageType.OPERATION_RECORD_RESULT)
    names = {"task_id": item["task_id"], "operation_id": item["operation_id"]}
    if record == "seal":
        return await link.ask(*ask, record=record, seal=body, **names)
    return await link.ask(*ask, record=record, readback=body, **names)


async def _read_back(link: _HubLink, item: dict[str, str], body: dict[str, Any]) -> str:
    """Record one read-back; return its result, or why it was not kept."""
    reply = await _record(link, item, "stop_readback", body)
    if reply is not None and reply.get("reason") == REFUSED_READBACK_CLOSED:
        print(f"attempt {item['attempt_id']}: a closing read-back is already recorded")
        return RECORDED_BEFORE
    if _hub_refusal(reply, f"attempt {item['attempt_id']}: read-back") is not None:
        return REFUSED
    assert reply is not None
    print(f"attempt {item['attempt_id']}: {body['result']} recorded at seq {reply.get('seq')}")
    return str(body["result"])


def _marker_fields(marker: AttemptMarker, fmt: str, read: str) -> dict[str, str]:
    return {"marker_ref": marker.ref, "object_format": fmt, "marker_object": read}


async def _seal_one(
    link: _HubLink, args: argparse.Namespace, git: str, stop_seq: int, item: dict[str, str]
) -> str:
    """Seal one listed attempt, read it back, and record what was read."""
    attempt_id = item["attempt_id"]
    state = await link.ask(
        MessageType.OPERATION_STATE,
        MessageType.OPERATION_STATE_RESULT,
        task_id=item["task_id"],
        operation_id=item["operation_id"],
    )
    if state is not None and state.get("reason") == "not_authorised":
        print(f"attempt {attempt_id}: {NOT_PERMITTED} on task {terminal_text(item['task_id'])}")
        return NOT_PERMITTED
    if _hub_refusal(state, f"attempt {attempt_id}: operation state") is not None:
        return REFUSED
    assert state is not None
    body: dict[str, Any] = {"stop_seq": stop_seq, "attempt_id": attempt_id}
    if state.get("effect") != _GIT_EFFECT:
        return await _read_back(link, item, {**body, "result": RESULT_UNSEALABLE})
    try:
        marker = AttemptMarker(
            item["operation_id"], attempt_id, str(state.get("intent_sha256") or "")
        )
    except AttemptMarkerError as exc:
        print(f"attempt {attempt_id}: no marker can be named: {terminal_text(str(exc))}")
        return REFUSED
    return await _seal_in_git(link, args, git, item, body, state, marker)


async def _seal_in_git(
    link: _HubLink,
    args: argparse.Namespace,
    git: str,
    item: dict[str, str],
    body: dict[str, Any],
    state: dict[str, Any],
    marker: AttemptMarker,
) -> str:
    """Seal one attempt in Git, read it back, and record what was read; the seal comes first."""
    attempt_id = item["attempt_id"]
    stop_seq = body["stop_seq"]
    declared = state.get("effect_target")
    target_fields = declared if isinstance(declared, dict) else {}
    # A seal does not move the branch, so the target's base is not read.
    revision = str(target_fields.get("revision") or "")
    target = Target(
        Path(args.repository), str(target_fields.get("branch") or ""), revision, args.remote
    )
    try:
        outcome = await asyncio.to_thread(seal, target, marker, git=git)
        fmt = await asyncio.to_thread(object_format, target, git=git)
    except GitError as exc:
        print(f"attempt {attempt_id}: seal state unknown: {terminal_text(str(exc))}")
        return await _read_back(link, item, {**body, "result": RESULT_UNREAD, "reason": UNKNOWN})
    if outcome.answer in (LANDED, LANDED_EARLIER) and outcome.marker is not None:
        if _attempt_state(state, attempt_id) == "open":
            seal_body = {
                **_marker_fields(marker, fmt, outcome.marker),
                "attempt_id": attempt_id,
                "observation": outcome.answer,
                "stop_seq": stop_seq,
            }
            kept = await _record(link, item, "seal", seal_body)
            ended_meanwhile = (
                kept is not None
                and kept.get("type") == MessageType.OPERATION_RECORD_RESULT
                and kept.get("status") == "refused"
                and kept.get("reason") == REFUSED_ATTEMPT_ENDED
            )
            if (
                not ended_meanwhile
                and _hub_refusal(kept, f"attempt {attempt_id}: seal record") is not None
            ):
                print(f"attempt {attempt_id}: the seal is in place at {marker.ref}; not recorded")
                return REFUSED
        fields = _marker_fields(marker, fmt, outcome.marker)
        return await _read_back(link, item, {**body, "result": RESULT_SEALED, **fields})
    if outcome.answer == OTHER_PARTY_FIRST and outcome.marker is not None:
        fields = _marker_fields(marker, fmt, outcome.marker)
        return await _read_back(link, item, {**body, "result": RESULT_APPLIED, **fields})
    reason = outcome.answer if outcome.answer in UNREAD_REASONS else UNKNOWN
    print(f"attempt {attempt_id}: the seal was not read back ({reason})")
    return await _read_back(link, item, {**body, "result": RESULT_UNREAD, "reason": reason})


async def _stop_and_seal(link: _HubLink, args: argparse.Namespace, git: str) -> int:
    stopped = await _stop(link, args)
    if isinstance(stopped, int):
        return stopped
    raw_seq = (
        stopped.get("standing_seq")
        if stopped.get("answer") == ALREADY_STOPPED
        else stopped.get("seq")
    )
    stop_seq = int(raw_seq or 0)
    listed = stopped.get("listed")
    items = [
        {key: str(item.get(key) or "") for key in ("task_id", "operation_id", "attempt_id")}
        for item in (listed if isinstance(listed, list) else [])
        if isinstance(item, dict)
    ]
    results = [await _seal_one(link, args, git, stop_seq, item) for item in items]
    closed = sum(result in (*CLOSING_RESULTS, RECORDED_BEFORE) for result in results)
    print(f"stop at journal seq {stop_seq}: {closed} of {len(items)} listed attempt(s) closed")
    return EXIT_DONE if closed == len(items) else EXIT_REFUSED


async def stop_and_seal(
    args: argparse.Namespace, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Run ``synapse operation stop --seal`` with parsed arguments and return its exit code."""
    try:
        git = git_executable()
    except GitError as exc:
        print(f"refused before contacting the hub: {terminal_text(str(exc))}")
        return EXIT_UNUSABLE
    link = _HubLink(args, agent_factory)
    try:
        if not await link.open():
            return EXIT_NO_VERDICT
        return await _stop_and_seal(link, args, git)
    finally:
        await link.close()
        print(f"request key: {args.idem_key}")

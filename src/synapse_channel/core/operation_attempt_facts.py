# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the current approval of an intent, read from the journal
"""Read the current approval of an intent digest from the hub journal.

An approval is a progress note of kind ``approval`` whose subject names the
digest, bare or as ``sha256:<hex>``. The latest such note decides: when it is
not ``approved``, the intent has no current approval. For an approved one the
hub also reads, from the same journal:

* whether it went through the verdict gate while the gate was off: a
  ``verdict_gate_bypass`` row by the same author on the same digest, after that
  author's previous note on it and before this one;
* why the verdict gate, evaluated now with the hub's own rules, would refuse
  it, using the approver's project namespace as the gate always does.

Nothing here writes. The caller reads under the hub's mutation lock, so the
notes cannot change between this reading and the answer it leads to.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, STATE_APPROVED, parse_approval_note
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempts import ApprovalFact
from synapse_channel.core.verdict_hub import approval_refusal, recorded_notes, subject_digest

if TYPE_CHECKING:
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.verdict_hub import VerdictHubPolicy


@dataclass(frozen=True)
class _Decision:
    seq: int
    author: str
    state: str


def _decisions_and_bypasses(
    store: EventStore, digest: str
) -> tuple[list[_Decision], list[tuple[int, str]]]:
    """Return the approval notes on ``digest`` and the gate bypass rows on it, in order."""
    decisions: list[_Decision] = []
    bypasses: list[tuple[int, str]] = []
    kinds = (EventKind.LEDGER_PROGRESS, EventKind.VERDICT_GATE_BYPASS)
    for event in store.iter_events(kinds=kinds):
        author = str(event.payload.get("author") or "")
        if event.kind == EventKind.VERDICT_GATE_BYPASS:
            if subject_digest(str(event.payload.get("subject") or "")) == digest:
                bypasses.append((event.seq, author))
            continue
        if str(event.payload.get("kind") or "") != APPROVAL_NOTE_KIND:
            continue
        fields = parse_approval_note(str(event.payload.get("text") or ""))
        if fields is not None and subject_digest(fields["subject"]) == digest:
            decisions.append(_Decision(event.seq, author, fields["state"]))
    return decisions, bypasses


def current_approval(
    store: EventStore, policy: VerdictHubPolicy, digest: str
) -> ApprovalFact | None:
    """Return the current approval of ``digest``, or ``None`` when it has none.

    Parameters
    ----------
    store : EventStore
        The hub journal.
    policy : VerdictHubPolicy
        The hub's verdict rules; the gate is evaluated as if it were on.
    digest : str
        The intent digest, 64 lower-case hexadecimal characters.

    Returns
    -------
    ApprovalFact or None
        The latest approval note on the digest with its bypass and gate
        findings, or ``None`` when no note names the digest or the latest one
        is a request or a rejection.
    """
    decisions, bypasses = _decisions_and_bypasses(store, digest)
    if not decisions or decisions[-1].state != STATE_APPROVED:
        return None
    latest = decisions[-1]
    earlier = max((item.seq for item in decisions[:-1] if item.author == latest.author), default=0)
    bypassed = any(
        earlier < seq < latest.seq and author == latest.author for seq, author in bypasses
    )
    refusal = approval_refusal(
        recorded_notes(store),
        replace(policy, gate_enabled=True),
        f"sha256:{digest}",
        approver=latest.author,
    )
    reasons = () if refusal is None else refusal.reasons
    return ApprovalFact(latest.seq, latest.author, bypassed, reasons)

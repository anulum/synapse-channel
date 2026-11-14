# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — chain continuity across the receipts of an AEF export
"""Check that the receipts of an AEF export form one unbroken chain per log.

Each AEF log numbers its receipts from 1 and links each to the one before:
``prev_receipt`` of seq 1 is :data:`GENESIS_RECEIPT`, and of every later seq it
is the ``receipt_id`` of seq − 1. A verdict on one receipt cannot see a missing
or replaced neighbour, so this check runs after the per-receipt verdicts and
never changes them.

Only authentic receipts take part: those whose content id and signature
verified, which are the ``VALID`` and ``EXPIRED`` ones (an expired receipt is
genuine, only old). A receipt that failed earlier leaves a hole, reported as a
gap. The findings, per log:

- ``CHAIN_GAP``: a seq between the lowest and the highest present is missing;
- ``CHAIN_BROKEN_LINK``: a receipt's ``prev_receipt`` is not the id of the
  receipt at seq − 1 that is present;
- ``CHAIN_FORK``: two different receipts hold the same seq, or name the same
  ``prev_receipt``;
- ``CHAIN_DUPLICATE``: the same receipt appears more than once;
- ``CHAIN_GENESIS_INVALID``: seq 1 does not link to the genesis id;
- ``CHAIN_OPEN_START``: informational; the lowest seq present is above 1, so
  the export cannot show what came before.

Without a signed tree head, an export cut short at its end cannot be told
from a complete one, and a later export that ends lower than an earlier one
needs state across exports; neither is detected here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.aef_verification import AefVerification
from synapse_channel.core.member_text import MemberText

GENESIS_RECEIPT = "aef1:" + "0" * 64
"""The ``prev_receipt`` of the first receipt of every AEF log."""

AUTHENTIC_VERDICTS = frozenset({AefVerdictCode.VALID, AefVerdictCode.EXPIRED})
"""Per-receipt verdicts reached only after the content id and signature verified."""


class AefChainCode(MemberText, StrEnum):
    """Findings of the chain check."""

    CHAIN_CONTINUOUS = "CHAIN_CONTINUOUS"
    CHAIN_OPEN_START = "CHAIN_OPEN_START"
    CHAIN_GAP = "CHAIN_GAP"
    CHAIN_BROKEN_LINK = "CHAIN_BROKEN_LINK"
    CHAIN_FORK = "CHAIN_FORK"
    CHAIN_DUPLICATE = "CHAIN_DUPLICATE"
    CHAIN_GENESIS_INVALID = "CHAIN_GENESIS_INVALID"


INFORMATIONAL = frozenset({AefChainCode.CHAIN_CONTINUOUS, AefChainCode.CHAIN_OPEN_START})
"""Findings that do not break a chain."""


@dataclass(frozen=True, slots=True)
class AefChainIssue:
    """One finding at one sequence number."""

    code: AefChainCode
    seq: int
    detail: str


@dataclass(frozen=True, slots=True)
class AefChainReport:
    """The chain check of one log in an export.

    Attributes
    ----------
    log_id : str
        The log the receipts belong to.
    first_seq, last_seq : int
        The lowest and highest seq among the authentic receipts.
    receipts : int
        Authentic receipts taking part, duplicates counted once.
    issues : tuple of AefChainIssue
        Every finding, informational ones included, in seq order.
    """

    log_id: str
    first_seq: int
    last_seq: int
    receipts: int
    issues: tuple[AefChainIssue, ...]

    @property
    def continuous(self) -> bool:
        """Whether no finding breaks the chain."""
        return all(issue.code in INFORMATIONAL for issue in self.issues)

    @property
    def verdict(self) -> AefChainCode:
        """``CHAIN_CONTINUOUS``, or the first finding that breaks the chain."""
        for issue in self.issues:
            if issue.code not in INFORMATIONAL:
                return issue.code
        return AefChainCode.CHAIN_CONTINUOUS


def verify_aef_chain(
    entries: Iterable[tuple[Mapping[str, object], AefVerification]],
) -> list[AefChainReport]:
    """Check chain continuity for every log among an export's authentic receipts.

    Parameters
    ----------
    entries : iterable of (receipt, verification)
        Each receipt of the export with its per-receipt verdict.

    Returns
    -------
    list of AefChainReport
        One report per log that has an authentic receipt, in ``log_id`` order.
    """
    logs: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for receipt, outcome in entries:
        if outcome.verdict in AUTHENTIC_VERDICTS:
            # An authentic receipt passed the structure check: seq is a positive int.
            seq = cast(int, receipt["seq"])
            logs[str(receipt["log_id"])].append(
                (seq, outcome.receipt_id, str(receipt["prev_receipt"]))
            )
    return [_report(log_id, logs[log_id]) for log_id in sorted(logs)]


def _report(log_id: str, members: list[tuple[int, str, str]]) -> AefChainReport:
    """Build the report of one log from its authentic receipts."""
    issues: list[AefChainIssue] = []
    counts = Counter(receipt_id for _seq, receipt_id, _prev in members)
    unique = {receipt_id: (seq, prev) for seq, receipt_id, prev in members}
    for receipt_id, count in counts.items():
        if count > 1:
            seq = unique[receipt_id][0]
            issues.append(
                AefChainIssue(AefChainCode.CHAIN_DUPLICATE, seq, f"{receipt_id} x{count}")
            )
    by_seq: dict[int, list[str]] = defaultdict(list)
    by_prev: dict[str, list[str]] = defaultdict(list)
    for receipt_id, (seq, prev) in unique.items():
        by_seq[seq].append(receipt_id)
        by_prev[prev].append(receipt_id)
    for seq, ids in by_seq.items():
        if len(ids) > 1:
            issues.append(
                AefChainIssue(AefChainCode.CHAIN_FORK, seq, "different receipts share it")
            )
    for prev, ids in by_prev.items():
        if len(ids) > 1:
            seq = max(unique[receipt_id][0] for receipt_id in ids)
            issues.append(AefChainIssue(AefChainCode.CHAIN_FORK, seq, f"several name {prev}"))
    first, last = min(by_seq), max(by_seq)
    if first == 1:
        if any(unique[receipt_id][1] != GENESIS_RECEIPT for receipt_id in by_seq[1]):
            issues.append(AefChainIssue(AefChainCode.CHAIN_GENESIS_INVALID, 1, "not genesis"))
    else:
        issues.append(AefChainIssue(AefChainCode.CHAIN_OPEN_START, first, "earlier seqs absent"))
    present = sorted(by_seq)
    issues.extend(_gaps(present))
    issues.extend(_broken_links(present, by_seq, unique))
    issues.sort(key=lambda issue: (issue.seq, issue.code.value))
    return AefChainReport(log_id, first, last, len(unique), tuple(issues))


def _gaps(present: list[int]) -> list[AefChainIssue]:
    """Return one gap finding per run of missing seqs between present ones.

    Only the sorted seqs present are walked, so the cost follows the number
    of receipts, not the width of the seq range.
    """
    gaps = []
    for before, after in zip(present, present[1:], strict=False):
        if after - before > 1:
            start, end = before + 1, after - 1
            span = f"{start}" if start == end else f"{start}..{end}"
            gaps.append(AefChainIssue(AefChainCode.CHAIN_GAP, start, f"seq {span} missing"))
    return gaps


def _broken_links(
    present: list[int],
    by_seq: Mapping[int, list[str]],
    unique: Mapping[str, tuple[int, str]],
) -> list[AefChainIssue]:
    """Return a finding for each receipt not linked to the receipt before it.

    A link is checked only where seq − 1 is present; a missing predecessor
    is a gap instead.
    """
    broken = []
    for seq in present:
        previous = by_seq.get(seq - 1)
        if previous is None:
            continue
        for receipt_id in by_seq[seq]:
            prev = unique[receipt_id][1]
            if prev not in previous:
                broken.append(
                    AefChainIssue(
                        AefChainCode.CHAIN_BROKEN_LINK, seq, f"prev {prev} not seq {seq - 1}"
                    )
                )
    return broken

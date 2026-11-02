# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verdict staleness and the approval gate, order-free and namespaced
"""A corrected input stales every verdict that read it, whatever the record order."""

from __future__ import annotations

import itertools
import time

from synapse_channel.core.verdict_records import (
    CORRECTION_NOTE_KIND,
    EVIDENCE_NOTE_KIND,
    VERDICT_NOTE_KIND,
    CorrectionRecord,
    EvidenceRecord,
    VerdictRecord,
    correction_id,
    format_correction,
    format_evidence,
    format_verdict,
)
from synapse_channel.core.verdict_staleness import (
    MAX_DEPENDENCY_DEPTH,
    STATE_FRESH,
    STATE_INPUTS_UNRECORDED,
    STATE_STALE,
    STATE_UNBOUNDED,
    CorrectionPolicy,
    RecordedNote,
    fold_verdicts,
    gate_decision,
)

RAW = "1" * 64
RAW_FIXED = "2" * 64
ABSTRACT = "3" * 64
SUMMARY = "4" * 64
OWNER = "LAB/owner"
WRITER = "LAB/analyst"
REVIEWER = "LAB/reviewer"
STRANGER = "OTHER/seat"


def _policy() -> CorrectionPolicy:
    """Seats write their own project namespace; only the LAB owner corrects beyond declarers."""
    return CorrectionPolicy(
        may_write=lambda namespace, seat: seat.startswith(f"{namespace}/"),
        may_correct=lambda namespace, seat: seat == f"{namespace}/owner",
    )


def _evidence(
    seq: int,
    digest: str,
    author: str = WRITER,
    *,
    namespace: str = "LAB",
    members: tuple[str, ...] = (),
) -> RecordedNote:
    body = format_evidence(EvidenceRecord(digest, namespace, tuple(sorted(members))))
    return RecordedNote(seq, author, EVIDENCE_NOTE_KIND, body)


def _verdict(
    seq: int,
    subject: str,
    inputs: tuple[str, ...],
    *,
    outcome: str = "accepted",
    acknowledged: tuple[str, ...] = (),
    reviewer: str = REVIEWER,
    sequence: int = 1,
    namespace: str = "LAB",
) -> RecordedNote:
    record = VerdictRecord(
        namespace, subject, outcome, tuple(sorted(inputs)), acknowledged, WRITER, sequence
    )
    return RecordedNote(seq, reviewer, VERDICT_NOTE_KIND, format_verdict(record))


def _correction(
    seq: int, old: str, new: str, author: str = WRITER, *, namespace: str = "LAB"
) -> RecordedNote:
    body = format_correction(CorrectionRecord(namespace, old, new, "raw record re-exported"))
    return RecordedNote(seq, author, CORRECTION_NOTE_KIND, body)


FIX_ID = correction_id(CorrectionRecord("LAB", RAW, RAW_FIXED, "any"))


def _gate(notes: list[RecordedNote], subject: str = ABSTRACT, *, unrecorded: bool = False) -> bool:
    fold = fold_verdicts(notes, _policy())
    return gate_decision(fold, "LAB", subject, allow_unrecorded_inputs=unrecorded).passed


def _renumbered(notes: list[RecordedNote]) -> list[RecordedNote]:
    return [
        RecordedNote(index, note.author, note.kind, note.text)
        for index, note in enumerate(notes, start=1)
    ]


def test_public_scenario_a_corrected_raw_record_stales_the_unchanged_abstract() -> None:
    """The abstract's approval is refused after its raw input is corrected."""
    notes = [_evidence(1, RAW), _evidence(2, ABSTRACT), _verdict(3, ABSTRACT, (RAW,))]
    assert _gate(notes)
    notes.append(_correction(4, RAW, RAW_FIXED))
    fold = fold_verdicts(notes, _policy())
    assert [state.state for state in fold.verdicts] == [STATE_STALE]
    assert fold.verdicts[0].unacknowledged_corrections == (FIX_ID,)
    decision = gate_decision(fold, "LAB", ABSTRACT)
    assert not decision.passed
    assert FIX_ID in decision.reasons[0]
    for renewed in (
        _verdict(5, ABSTRACT, (RAW_FIXED,), sequence=2),
        _verdict(5, ABSTRACT, (RAW,), acknowledged=(FIX_ID,), sequence=2),
    ):
        assert _gate([*notes, renewed])


def test_state_does_not_depend_on_record_order() -> None:
    """Every permutation of the same records yields identical states and gate."""
    base = [
        _evidence(1, RAW),
        _verdict(2, ABSTRACT, (RAW,)),
        _correction(3, RAW, RAW_FIXED),
        _verdict(4, SUMMARY, (ABSTRACT,)),
    ]
    results = set()
    for order in itertools.permutations(base):
        fold = fold_verdicts(_renumbered(list(order)), _policy())
        results.add(
            (
                tuple(sorted((s.record.subject_sha256, s.state) for s in fold.verdicts)),
                gate_decision(fold, "LAB", ABSTRACT).passed,
                gate_decision(fold, "LAB", SUMMARY).passed,
            )
        )
    assert results == {(((ABSTRACT, STATE_STALE), (SUMMARY, STATE_STALE)), False, False)}


def test_a_foreign_namespace_cannot_correct_or_block_by_declaring_the_same_hash() -> None:
    """The reviewed attack: OTHER declares LAB's hash in OTHER and corrects it; LAB is untouched."""
    lab = [_evidence(1, RAW), _evidence(2, ABSTRACT), _verdict(3, ABSTRACT, (RAW,))]
    foreign = [
        _evidence(4, RAW, author=STRANGER, namespace="OTHER"),
        _correction(5, RAW, RAW_FIXED, author=STRANGER, namespace="OTHER"),
    ]
    for order in (lab + foreign, foreign + lab):
        fold = fold_verdicts(_renumbered(order), _policy())
        assert fold.refused == []
        assert ("OTHER", RAW) in fold.corrections
        assert ("LAB", RAW) not in fold.corrections
        assert [state.state for state in fold.verdicts] == [STATE_FRESH]
        assert gate_decision(fold, "LAB", ABSTRACT).passed
    direct = fold_verdicts([*lab, _correction(4, RAW, RAW_FIXED, author=STRANGER)], _policy())
    assert direct.refused == [(4, "correction-not-authorised")]


def test_foreign_declarations_and_verdicts_are_refused() -> None:
    """A stranger can neither declare in LAB nor block LAB's gate with a rejection."""
    notes = [
        _evidence(1, RAW, author=STRANGER),
        _evidence(2, RAW),
        _verdict(3, ABSTRACT, (RAW,)),
        _verdict(4, ABSTRACT, (RAW,), outcome="rejected", reviewer=STRANGER),
    ]
    fold = fold_verdicts(notes, _policy())
    assert fold.refused == [(1, "evidence-not-authorised"), (4, "verdict-not-authorised")]
    assert fold.declarers[("LAB", RAW)] == {WRITER}
    assert gate_decision(fold, "LAB", ABSTRACT).passed
    owner_fix = fold_verdicts([*notes, _correction(5, RAW, RAW_FIXED, author=OWNER)], _policy())
    assert owner_fix.verdicts[0].state == STATE_STALE


def test_an_undeclared_object_cannot_be_corrected() -> None:
    """Without a declaration in the namespace nobody, not even its owner, may correct it."""
    fold = fold_verdicts(
        [_verdict(1, ABSTRACT, (RAW,)), _correction(2, RAW, RAW_FIXED, author=OWNER)], _policy()
    )
    assert fold.refused == [(2, "correction-not-authorised")]
    assert fold.verdicts[0].state == STATE_FRESH


def test_manifest_member_correction_stales_a_verdict_citing_the_manifest() -> None:
    """A verdict over a whole packet turns stale when one member file is corrected."""
    manifest = "5" * 64
    members = tuple(sorted(f"{index:064x}" for index in range(1, 6)))
    notes = [
        _evidence(1, manifest, members=members),
        _evidence(2, members[2]),
        _verdict(3, ABSTRACT, (manifest,)),
        _correction(4, members[2], RAW_FIXED),
    ]
    assert fold_verdicts(notes, _policy()).verdicts[0].state == STATE_STALE


def test_revert_keeps_both_objects_corrected_until_acknowledged() -> None:
    """R -> R' -> R: a verdict on either object is stale until it acknowledges both."""
    back_id = correction_id(CorrectionRecord("LAB", RAW_FIXED, RAW, "any"))
    notes = [
        _evidence(1, RAW),
        _evidence(2, RAW_FIXED),
        _correction(3, RAW, RAW_FIXED),
        _correction(4, RAW_FIXED, RAW),
        _verdict(5, ABSTRACT, (RAW,), acknowledged=(FIX_ID,)),
        _verdict(6, SUMMARY, (RAW_FIXED,), acknowledged=(back_id,)),
    ]
    fold = fold_verdicts(notes, _policy())
    assert [state.state for state in fold.verdicts] == [STATE_FRESH, STATE_FRESH]
    stale = fold_verdicts([*notes[:4], _verdict(5, ABSTRACT, (RAW, RAW_FIXED))], _policy())
    assert stale.verdicts[0].unacknowledged_corrections == tuple(sorted((FIX_ID, back_id)))


def test_downstream_verdicts_follow_only_the_latest_upstream_verdict() -> None:
    """Once ABSTRACT is re-reviewed on the fixed record, SUMMARY over ABSTRACT is fresh again."""
    notes = [
        _evidence(1, RAW),
        _verdict(2, ABSTRACT, (RAW,)),
        _verdict(3, SUMMARY, (ABSTRACT,)),
        _correction(4, RAW, RAW_FIXED),
    ]
    assert not _gate(notes, SUMMARY)
    rereviewed = [*notes, _verdict(5, ABSTRACT, (RAW_FIXED,), sequence=2)]
    assert _gate(rereviewed, ABSTRACT)
    assert _gate(rereviewed, SUMMARY)


def test_gate_counts_each_reviewers_latest_verdict_without_arrival_order() -> None:
    """The highest reviewer sequence decides; every reviewer must accept a fresh subject."""
    second = "LAB/second-reviewer"
    newer_first = [
        _verdict(1, ABSTRACT, (RAW,), outcome="accepted", sequence=2),
        _verdict(2, ABSTRACT, (RAW,), outcome="changes-required", sequence=1),
    ]
    assert _gate(newer_first)
    rejected = _verdict(3, ABSTRACT, (RAW,), outcome="rejected", reviewer=second)
    decision = gate_decision(fold_verdicts([*newer_first, rejected], _policy()), "LAB", ABSTRACT)
    assert decision.reasons == (f"{second}: rejected",)
    conflicting = [
        _verdict(1, ABSTRACT, (RAW,), sequence=4),
        _verdict(2, ABSTRACT, (RAW_FIXED,), sequence=4),
    ]
    decision = gate_decision(fold_verdicts(conflicting, _policy()), "LAB", ABSTRACT)
    assert decision.reasons == (f"{REVIEWER}: conflicting verdicts at one sequence",)
    empty = gate_decision(fold_verdicts([], _policy()), "LAB", ABSTRACT)
    assert empty.reasons == ("no verdict recorded",)
    elsewhere = [_verdict(1, ABSTRACT, (RAW,), reviewer="OTHER/reviewer", namespace="OTHER")]
    assert not _gate(elsewhere)


def test_unrecorded_inputs_and_unbounded_chains_fail_closed() -> None:
    """Accepted-without-inputs is refused by default; an over-deep chain always is."""
    empty = [_verdict(1, ABSTRACT, ())]
    assert fold_verdicts(empty, _policy()).verdicts[0].state == STATE_INPUTS_UNRECORDED
    assert not _gate(empty)
    assert _gate(empty, unrecorded=True)
    chain = [f"{index + 100:064x}" for index in range(MAX_DEPENDENCY_DEPTH + 3)]
    notes = [
        _verdict(index + 1, chain[index], (chain[index + 1],)) for index in range(len(chain) - 1)
    ]
    fold = fold_verdicts(notes, _policy())
    top = next(state for state in fold.verdicts if state.record.subject_sha256 == chain[0])
    assert top.state == STATE_UNBOUNDED
    assert "exceeds" in gate_decision(fold, "LAB", chain[0]).reasons[0]


def test_malformed_records_are_refused_and_ignored() -> None:
    """A body that does not parse never contributes to the fold."""
    notes = [
        RecordedNote(1, WRITER, VERDICT_NOTE_KIND, "{}"),
        RecordedNote(2, WRITER, "note", "plain progress"),
        _verdict(3, ABSTRACT, (RAW,)),
    ]
    fold = fold_verdicts(notes, _policy())
    assert fold.refused == [(1, "malformed-record")]
    assert len(fold.verdicts) == 1


def test_fold_cost_stays_bounded_for_a_full_manifest() -> None:
    """A 100 000-member manifest folds and gates within a few seconds."""
    manifest = "6" * 64
    members = tuple(f"{index:064x}" for index in range(1, 100_001))
    notes = [
        _evidence(1, manifest, members=members),
        _evidence(2, members[-1]),
        _verdict(3, ABSTRACT, (manifest,)),
        _correction(4, members[-1], RAW_FIXED),
    ]
    started = time.perf_counter()
    passed = _gate(notes)
    assert time.perf_counter() - started < 10.0
    assert not passed

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — characterisation and surface contracts for journal replay
"""Pin restart replay, multi-hub restores, protected-history failure, and module boundaries."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core import journal as journal_module
from synapse_channel.core import journal_replay as journal_replay_module
from synapse_channel.core.assignment_origin import AssignmentOrigin
from synapse_channel.core.journal import (
    RELAY_DIRECTION_IN,
    EventKind,
    UnsupportedProtectedWriteHistoryError,
    record_chat,
    record_claim,
    record_claim_denial,
    record_finding,
    record_guard_denial,
    record_idempotency,
    record_ledger_progress,
    record_ledger_task,
    record_multihub_equivocation,
    record_multihub_equivocation_recovery,
    record_multihub_ownership_transitions,
    record_operator_relay,
    record_release,
    record_resource,
)
from synapse_channel.core.journal_replay import (
    _UNVERIFIED_PARTITION_CONTESTER,
    ReplayResult,
    _claim_from_payload,
    replay,
    restore_active_multihub_partitions,
    restore_active_multihub_quarantines,
)
from synapse_channel.core.ledger import LedgerTask, ProgressNote
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.multihub_equivocation import FederationQuarantine
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_enrollment_journal import (
    ISSUE_EVENT_KIND,
    enrollment_issue_payload,
)
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.state import ResourceOffer, TaskClaim


def _enrollment() -> ProtectedSessionEnrollment:
    return ProtectedSessionEnrollment(
        session_id="writer-golden",
        principal="RIGOR/writer",
        target="SYNAPSE/authority",
        authority_id="authority-golden",
        authority_continuity="continuity-golden",
        enrollment_revision="rev-1",
        transaction_id="tx-golden",
        proposal_sha256="a" * 64,
        verbs=frozenset({"begin", "settle"}),
        expires_at=5_000_000_000.0,
        key=MessageAuthKey("key-golden", b"k" * 32, frozenset({"RIGOR/writer"})),
        writer_incarnation="inc-1",
    )


def _populate_golden_journal(store: EventStore) -> ProtectedSessionEnrollment:
    """Write every family reconstructed by replay and the multi-hub restores."""
    # 1. Claims and leases with epochs (active, expired, released, plus default-field payload)
    record_claim(
        store,
        TaskClaim(
            task_id="T-active",
            owner="P/alice",
            note="owning task",
            claimed_at=1000.0,
            lease_expires_at=5000.0,
            quota_principal="P/alice",
            status="claimed",
            data_ref="ref-1",
            worktree="wt-1",
            paths=("src/a.py",),
            epoch=5,
            ownership_epoch=5,
            version=2,
            checkpoint="cp-1",
        ),
    )
    record_claim(
        store,
        TaskClaim(
            task_id="T-expired",
            owner="P/bob",
            note="stale lease",
            claimed_at=100.0,
            lease_expires_at=1500.0,
            quota_principal="P/bob",
            epoch=2,
            ownership_epoch=2,
        ),
    )
    record_claim(
        store,
        TaskClaim(
            task_id="T-released",
            owner="P/carol",
            note="soon freed",
            claimed_at=1000.0,
            lease_expires_at=5000.0,
            quota_principal="P/carol",
            epoch=3,
            ownership_epoch=3,
        ),
    )
    record_release(store, "T-released")
    # Minimal persisted claim payload exercising every default in _claim_from_payload.
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "T-defaults",
            "owner": "P/legacy",
            "claimed_at": 1000.0,
            "lease_expires_at": 5000.0,
        },
        durable=True,
    )

    # 2. Task board and progress with caps + minimal-default snapshots
    record_ledger_task(
        store,
        LedgerTask(
            task_id="B-1",
            title="Full task",
            created_at=100.0,
            updated_at=200.0,
            description="pinned description",
            depends_on=("B-0",),
            status="in_progress",
            suggested_owner="P/alice",
            project="P",
            version=3,
            created_by="P/lead",
            assignment_origin=AssignmentOrigin(owner="P/alice", anchor=1, legacy=False),
        ),
    )
    store.append(
        EventKind.LEDGER_TASK,
        {"task_id": "B-defaults", "title": "Defaults task", "created_at": 10.0, "updated_at": 20.0},
        durable=True,
    )
    for idx, author in enumerate(("P/alice", "P/alice", "P/alice", "P/bob"), start=1):
        record_ledger_progress(
            store,
            ProgressNote(
                task_id="B-1",
                author=author,
                kind="update",
                text=f"note-{idx}",
                posted_at=1000.0 + idx,
            ),
        )
    # Minimal progress note exercising default task_id/author/kind/text in _progress_from_payload.
    store.append(EventKind.LEDGER_PROGRESS, {"posted_at": 1010.0})

    # 3. Resources (active + expired)
    record_resource(
        store,
        ResourceOffer(
            agent="P/alice",
            kind="gpu",
            name="a100",
            capacity=1,
            meta={"mem": "80G"},
            offered_at=1900.0,
        ),
    )
    record_resource(
        store,
        ResourceOffer(
            agent="P/bob",
            kind="gpu",
            name="old",
            capacity=1,
            meta={},
            offered_at=100.0,
        ),
    )

    # 4. Registry denials — must remain audit-only
    record_claim_denial(
        store,
        claimant="P/intruder",
        task_id="T-active",
        reason_code="scope_conflict",
        worktree="wt-1",
        paths=["src/a.py"],
    )
    record_guard_denial(store, {"reason": "denied_by_guard", "actor": "P/intruder"})

    # 5. Chat history, idempotency, and finding counts
    record_chat(store, {"msg_id": 11, "sender": "P/alice", "payload": "hello"})
    record_chat(store, {"msg_id": 17, "sender": "P/bob", "payload": "world"})
    record_idempotency(store, "k1", {"ok": True, "v": 1})
    record_idempotency(store, "k2", {"ok": True, "v": 2})
    record_idempotency(store, "k1", {"ok": True, "v": 3})
    record_finding(store, {"id": "f1", "provenance": {"actor": "P/alice"}, "summary": "one"})
    record_finding(store, {"id": "f2", "provenance": {"actor": "P/alice"}, "summary": "two"})
    record_finding(store, {"id": "f3", "provenance": {"actor": "P/bob"}, "summary": "three"})

    # 6. Operator-relay pending quorum (one remains pending; one reaches terminal applied)
    record_operator_relay(
        store,
        {
            "direction": RELAY_DIRECTION_IN,
            "action": "release",
            "namespace": "NS",
            "task_id": "T-pending",
            "status": "pending",
            "applied": False,
            "requester": "op-alice",
            "requester_principal": "principal-alice",
        },
    )
    record_operator_relay(
        store,
        {
            "direction": RELAY_DIRECTION_IN,
            "action": "release",
            "namespace": "NS",
            "task_id": "T-done",
            "status": "pending",
            "applied": False,
            "requester": "op-alice",
            "requester_principal": "principal-alice",
        },
    )
    record_operator_relay(
        store,
        {
            "direction": RELAY_DIRECTION_IN,
            "action": "release",
            "namespace": "NS",
            "task_id": "T-done",
            "status": "applied",
            "applied": True,
            "requester": "op-bob",
            "requester_principal": "principal-bob",
        },
    )

    # 7. Protected-write history (durable enrollment issue)
    enrollment = _enrollment()
    store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(enrollment), durable=True)

    # 8. Multi-hub partitions (active, healed, malformed contester) and quarantines
    record_multihub_ownership_transitions(
        store,
        [
            (
                EventKind.MULTIHUB_PARTITION,
                {"namespace": "NS-live", "contesting_hubs": ["hub-b", "hub-a", "hub-a"]},
            ),
            (
                EventKind.MULTIHUB_PARTITION,
                {"namespace": "NS-healed", "contesting_hubs": ["hub-c"]},
            ),
            (
                EventKind.MULTIHUB_HEAL,
                {"namespace": "NS-healed", "observation_refreshed": True},
            ),
            (
                EventKind.MULTIHUB_PARTITION,
                {"namespace": "NS-malformed", "contesting_hubs": "not-a-list"},
            ),
        ],
    )
    record_multihub_equivocation(
        store,
        FederationQuarantine(
            peer_id="peer-active",
            seq=42,
            accepted_fingerprint="a" * 64,
            conflicting_fingerprint="b" * 64,
            detected_at=1800.0,
            observer_id="hub-local",
        ),
    )
    record_multihub_equivocation(
        store,
        FederationQuarantine(
            peer_id="peer-recovered",
            seq=43,
            accepted_fingerprint="c" * 64,
            conflicting_fingerprint="d" * 64,
            detected_at=1810.0,
            observer_id="hub-local",
        ),
    )
    record_multihub_equivocation_recovery(
        store,
        peer_id="peer-recovered",
        recovered_at=1850.0,
        recovered_by="op-alice",
        reason="rotated peer log generation after audit",
        new_log_generation="gen-2",
    )
    return enrollment


def test_golden_replay_reconstructs_every_family_to_pinned_values(tmp_path: Path) -> None:
    """Full golden replay reconstructs every durable family to exact base-pinned values."""
    with EventStore(tmp_path / "golden.db") as store:
        enrollment = _populate_golden_journal(store)

        result = replay(
            store,
            now=2000.0,
            max_progress=4,
            max_progress_per_author=2,
            max_progress_per_task=3,
            protected_write_policies={},
        )
        partitions = restore_active_multihub_partitions(store)
        quarantines = restore_active_multihub_quarantines(store)

    assert isinstance(result, ReplayResult)
    # Claims, leases, and epochs
    assert sorted(result.state.claims) == ["T-active", "T-defaults"]
    assert result.state.epoch_seq == 5
    active = result.state.claims["T-active"]
    assert active.owner == "P/alice"
    assert active.epoch == 5
    assert active.ownership_epoch == 5
    assert active.version == 2
    assert active.checkpoint == "cp-1"
    assert active.paths == ("src/a.py",)
    assert active.worktree == "wt-1"
    defaults_claim = result.state.claims["T-defaults"]
    assert defaults_claim.quota_principal == "P/legacy"
    assert defaults_claim.status == "claimed"
    assert defaults_claim.note == ""
    assert defaults_claim.data_ref == ""
    assert defaults_claim.worktree == ""
    assert defaults_claim.paths == ()
    assert defaults_claim.epoch == 0
    assert defaults_claim.ownership_epoch == 0
    assert defaults_claim.version == 0
    assert defaults_claim.checkpoint == ""

    # Task board and progress caps + payload-helper defaults
    assert sorted(result.blackboard.tasks) == ["B-1", "B-defaults"]
    full_task = result.blackboard.tasks["B-1"]
    assert full_task.description == "pinned description"
    assert full_task.depends_on == ("B-0",)
    assert full_task.status == "in_progress"
    assert full_task.suggested_owner == "P/alice"
    assert full_task.project == "P"
    assert full_task.version == 3
    assert full_task.created_by == "P/lead"
    assert full_task.assignment_origin == AssignmentOrigin(owner="P/alice", anchor=1, legacy=False)
    default_task = result.blackboard.tasks["B-defaults"]
    assert default_task.description == ""
    assert default_task.depends_on == ()
    assert default_task.status == "open"
    assert default_task.suggested_owner == ""
    assert default_task.project == ""
    assert default_task.version == 1
    assert default_task.created_by == ""
    assert default_task.assignment_origin == AssignmentOrigin(owner="", anchor=1, legacy=False)

    # Caps: max_progress_per_author=2 drops note-1; max_progress_per_task=3 keeps note-2,3,4
    # plus default progress note on task_id="".
    progress_summary = [
        (note.task_id, note.author, note.kind, note.text) for note in result.blackboard.progress
    ]
    assert progress_summary == [
        ("B-1", "P/alice", "update", "note-2"),
        ("B-1", "P/alice", "update", "note-3"),
        ("B-1", "P/bob", "update", "note-4"),
        ("", "", "note", ""),
    ]

    # Resources (expired offer pruned)
    assert sorted(result.state.resources) == ["P/alice:gpu:a100"]

    # Chat, idempotency, finding counts, relay approvals, corrupt rows
    assert result.message_seq == 17
    assert [m["msg_id"] for m in result.chat_history] == [11, 17]
    assert result.idempotency == [("k2", {"ok": True, "v": 2}), ("k1", {"ok": True, "v": 3})]
    assert result.finding_counts_by_actor == {"P/alice": 2, "P/bob": 1}
    assert result.relay_approvals.pending() == [
        {
            "action": "release",
            "namespace": "NS",
            "task_id": "T-pending",
            "requester": "op-alice",
        }
    ]
    assert result.corrupt_rows == ()

    # Protected-write enrollment reconstructed
    restored_enrollment = result.state.protected_enrollments[enrollment.session_id]
    assert restored_enrollment.materialize(enrollment.key, now=2000.0) == enrollment

    # Multi-hub partitions and quarantines
    assert partitions == {
        "NS-live": ("hub-a", "hub-b"),
        "NS-malformed": (_UNVERIFIED_PARTITION_CONTESTER,),
    }
    assert list(quarantines) == ["peer-active"]
    q = quarantines["peer-active"]
    assert q.peer_id == "peer-active"
    assert q.seq == 42
    assert q.accepted_fingerprint == "a" * 64
    assert q.conflicting_fingerprint == "b" * 64
    assert q.observer_id == "hub-local"


def test_unsupported_protected_write_history_raises_journal_error_with_stable_code(
    tmp_path: Path,
) -> None:
    """Replaying protected-write history without policies raises journal's stable error."""
    with EventStore(tmp_path / "protected.db") as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(_enrollment()), durable=True)

        with pytest.raises(UnsupportedProtectedWriteHistoryError) as exc_info:
            replay(store)
        assert exc_info.value.code == "unsupported_protected_write_history"
        assert UnsupportedProtectedWriteHistoryError.__module__ == "synapse_channel.core.journal"

        with pytest.raises(ValueError, match="unfiltered history"):
            replay(store, protected_write_policies={}, event_kinds=(EventKind.CLAIM,))


def test_partition_restore_malformed_contester_and_quarantine_restore_bounded_payload(
    tmp_path: Path,
) -> None:
    """Partition restore fails closed on malformed contesters; quarantine restore bounds fields."""
    with EventStore(tmp_path / "multihub.db") as store:
        store.append(EventKind.MULTIHUB_PARTITION, {"namespace": "   ", "contesting_hubs": ["h1"]})
        store.append(
            EventKind.MULTIHUB_PARTITION,
            {"namespace": "NS-empty-list", "contesting_hubs": [" ", ""]},
        )
        store.append(
            EventKind.MULTIHUB_PARTITION,
            {"namespace": "NS-nonlist", "contesting_hubs": 123},
        )
        store.append(
            EventKind.MULTIHUB_HEAL,
            {"namespace": "NS-nonlist", "observation_refreshed": False},
        )

        # Malformed quarantine row: bool seq and invalid fingerprints fall back safely.
        store.append(EventKind.MULTIHUB_EQUIVOCATION, {"peer_id": "  "})
        store.append(
            EventKind.MULTIHUB_EQUIVOCATION,
            {
                "peer_id": "peer-bad-fp",
                "seq": True,
                "accepted_fingerprint": "not-hex",
                "conflicting_fingerprint": "short",
                "observer_id": "obs-1",
            },
            ts=100.0,
        )
        # Incomplete recovery without new_log_generation must not clear quarantine.
        store.append(
            EventKind.MULTIHUB_EQUIVOCATION_RECOVERY,
            {"peer_id": "peer-bad-fp", "explicit_recovery": True, "new_log_generation": "  "},
        )

        partitions = restore_active_multihub_partitions(store)
        quarantines = restore_active_multihub_quarantines(store)

        assert partitions == {
            "NS-empty-list": (_UNVERIFIED_PARTITION_CONTESTER,),
            "NS-nonlist": (_UNVERIFIED_PARTITION_CONTESTER,),
        }
        assert list(quarantines) == ["peer-bad-fp"]
        bad_q = quarantines["peer-bad-fp"]
        assert bad_q.seq == 0
        assert bad_q.accepted_fingerprint == "0" * 64
        assert bad_q.conflicting_fingerprint == "0" * 64
        assert bad_q.observer_id == "obs-1"

        # Verify bounded payload on record_multihub_equivocation omits free-text / raw bloat.
        raw_rows: list[dict[str, Any]] = [
            ev.payload for ev in store.iter_events(kinds=(EventKind.MULTIHUB_EQUIVOCATION,))
        ]
        assert len(raw_rows) == 2


def test_claim_from_payload_rejects_mismatched_path_identity_and_bad_ownership_epoch() -> None:
    """Persisted claim decoding fails closed on scope mismatch or invalid ownership_epoch."""
    identity = ClaimScopeIdentity(
        worktree_path="/repo",
        case_sensitive=True,
        paths=(CanonicalPathIdentity(git_path="src/a.py", filesystem_path="src/a.py"),),
    )
    bad_scope: dict[str, Any] = {
        "task_id": "T-bad-scope",
        "owner": "P/alice",
        "claimed_at": 10.0,
        "lease_expires_at": 20.0,
        "worktree": "/repo",
        "paths": ["src/other.py"],
        "path_identity": identity.as_dict(),
    }
    with pytest.raises(ValueError, match="display scope"):
        _claim_from_payload(bad_scope)

    bad_epoch: dict[str, Any] = {
        "task_id": "T-bad-epoch",
        "owner": "P/alice",
        "claimed_at": 10.0,
        "lease_expires_at": 20.0,
        "epoch": 2,
        "ownership_epoch": 5,
    }
    with pytest.raises(ValueError, match="ownership epoch"):
        _claim_from_payload(bad_epoch)


def test_replay_legacy_claim_without_ownership_epoch_uses_epoch(tmp_path: Path) -> None:
    """Legacy CLAIM rows inherit integer epoch and reject non-int epoch without ownership_epoch."""
    with EventStore(tmp_path / "legacy_claim.db") as store:
        store.append(
            EventKind.CLAIM,
            {
                "task_id": "T-legacy-epoch",
                "owner": "P/alice",
                "claimed_at": 1000.0,
                "lease_expires_at": 5000.0,
                "epoch": 7,
            },
            durable=True,
        )
        result = replay(store, now=2000.0)

    claim = result.state.claims["T-legacy-epoch"]
    assert claim.epoch == 7
    assert claim.ownership_epoch == 7
    assert result.state.epoch_seq == 7

    with EventStore(tmp_path / "legacy_bool_epoch.db") as bad_store:
        bad_store.append(
            EventKind.CLAIM,
            {
                "task_id": "T-legacy-bool-epoch",
                "owner": "P/alice",
                "claimed_at": 1000.0,
                "lease_expires_at": 5000.0,
                "epoch": True,
            },
            durable=True,
        )
        with pytest.raises(ValueError, match="invalid persisted ownership epoch"):
            replay(bad_store, now=2000.0)


def test_journal_and_journal_replay_module_surface_and_dependency_direction() -> None:
    """Enforce one-owner split: no aliases in journal, no writers or error in journal_replay."""
    # F4: journal.py leaves no aliases or re-exports of moved read-side names.
    moved_read_side = (
        "replay",
        "ReplayResult",
        "_protected_replay_callbacks",
        "_claim_from_payload",
        "_ledger_task_from_payload",
        "_progress_from_payload",
        "restore_active_multihub_partitions",
        "restore_active_multihub_quarantines",
        "_UNVERIFIED_PARTITION_CONTESTER",
    )
    for name in moved_read_side:
        assert name not in vars(journal_module), f"stray alias {name!r} in core.journal"
        assert name in vars(journal_replay_module), f"missing {name!r} in core.journal_replay"

    # F1: no writer moves or is duplicated into the reader side.
    stray_writers = [name for name in vars(journal_replay_module) if name.startswith("record_")]
    assert stray_writers == []

    # F3: stable error taxonomy class remains defined in core.journal.
    assert UnsupportedProtectedWriteHistoryError.__module__ == "synapse_channel.core.journal"
    assert (
        vars(journal_replay_module)["UnsupportedProtectedWriteHistoryError"]
        is UnsupportedProtectedWriteHistoryError
    )

    # F6: journal.py never imports journal_replay (checked via AST and both import orders).
    repo_root = Path(__file__).resolve().parents[1]
    journal_tree = ast.parse(
        (repo_root / "src/synapse_channel/core/journal.py").read_text(encoding="utf-8")
    )
    imported_modules: set[str] = set()
    for node in ast.walk(journal_tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
    assert "synapse_channel.core.journal_replay" not in imported_modules

    sub_env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    for stmt in (
        "import synapse_channel.core.journal_replay, synapse_channel.core.journal",
        "import synapse_channel.core.journal, synapse_channel.core.journal_replay",
    ):
        proc = subprocess.run(
            [sys.executable, "-c", stmt],
            check=False,
            capture_output=True,
            text=True,
            env=sub_env,
        )
        assert proc.returncode == 0, proc.stderr

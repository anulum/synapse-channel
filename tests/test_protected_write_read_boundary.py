# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual journal and captured-byte read coherence
"""Exercise the public snapshot API against real authority, SQLite and file mutations."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_descriptors import hold_protected_path
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_inspection import inspect_protected_file
from synapse_channel.core.protected_write_read_boundary import (
    ProtectedReadLimits,
    ProtectedReadSnapshot,
    read_at_protected_write_boundary,
)
from synapse_channel.core.state import SynapseState
from test_protected_write_admission_journal import POLICIES, _committed
from test_protected_write_session_auth import KEY, NOW, enrollment
from test_protected_write_transition_handler import (
    AUTHOR_KEY,
    _authenticated_transition,
    _enrollment,
    _handler,
)

LIMITS = ProtectedReadLimits(32, 4, 1024, 128, 2.0)


@pytest.mark.asyncio
async def test_snapshot_retains_actual_file_bytes_while_unrelated_chat_continues(
    tmp_path: Path,
) -> None:
    """One hundred actual chat rows do not invalidate a detached stable content snapshot."""
    record = tmp_path / "record"
    record.write_bytes(b"captured file content\n")
    source: dict[str, bytes] = {}
    with EventStore(tmp_path / "authority.db") as journal:
        journal.append("chat", {"message": "before"})
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)

        async def collect() -> dict[str, bytes]:
            """Read the real record while appending unrelated actor-ordered journal traffic."""
            source["record"] = record.read_bytes()
            for _ in range(100):
                await hub.state_mutations.run(
                    hub.state,
                    lambda _state: journal.append("chat", {"message": "unrelated"}),
                )
            return source

        result = await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)
        assert dict(result.content) == {"record": b"captured file content\n"}
        assert (result.before_sequence, result.after_sequence) == (1, 101)
        source["record"] = b"different caller dictionary"
        record.write_bytes(b"later pathname bytes")
        assert result.content["record"] == b"captured file content\n"


@pytest.mark.asyncio
async def test_real_pending_admission_refuses_read_until_durable_prebegin_revoke(
    tmp_path: Path,
) -> None:
    """Real admitted custody blocks collection; actual authenticated no-write revoke releases it."""
    with _committed(tmp_path) as journal:
        hub = SynapseHub(
            journal=journal, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        entered = False

        async def collect() -> dict[str, bytes]:
            """Observe collector entry before and after durable revoke."""
            nonlocal entered
            entered = True
            return {"record": b"unchanged before any effect"}

        with pytest.raises(ValueError, match="outstanding write custody"):
            await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)
        assert not entered
        grant = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({"revoke"}))
        registry = {grant.session_id: grant}
        handler = _handler(hub, registry)
        request = _authenticated_transition(
            "revoke", grant=grant, key=AUTHOR_KEY, nonce="reader-prebegin", registry=registry
        )
        body = json.loads(await handler(request))["body"]
        assert body["outcome"] == "no_write"
        result = await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)
        assert entered and result.content["record"] == b"unchanged before any effect"


@pytest.mark.asyncio
async def test_issue_and_revoke_during_collection_cannot_hide_behind_equal_final_state(
    tmp_path: Path,
) -> None:
    """Two actual ordered enrollment mutations invalidate a read even after enrollment is empty."""
    with EventStore(tmp_path / "authority.db") as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={"session": KEY}, clock=lambda: NOW
        )

        async def collect() -> dict[str, bytes]:
            """Change real enrollment history while restoring its final registry to empty."""
            await control.issue(enrollment())
            await control.revoke("session")
            assert control.current() == {}
            return {"record": b"must not be returned"}

        with pytest.raises(ValueError, match="crossed a protected mutation"):
            await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)
        assert len(journal.read_all()) == 2


@pytest.mark.parametrize("change", ["journal", "state", "checkpoint"])
@pytest.mark.asyncio
async def test_replaced_authority_or_removed_checkpoint_refuses_result(
    tmp_path: Path, change: str
) -> None:
    """Authority replacement or retained-event deletion cannot validate old content."""
    with (
        EventStore(tmp_path / "authority.db") as journal,
        EventStore(tmp_path / "other.db") as other,
    ):
        baseline = journal.append("chat", {"message": "anchor"})
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)

        async def collect() -> dict[str, bytes]:
            """Replace the journal/state or remove a checkpoint during the actual collection
            interval.
            """
            if change == "journal":
                hub.journal = other
            elif change == "state":
                hub.state = SynapseState()
            else:
                assert journal.delete([baseline]) == 1
            return {"record": b"must not be returned"}

        with pytest.raises(ValueError, match="authority or retained checkpoint changed"):
            await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)


@pytest.mark.parametrize("good_tail", [False, True])
@pytest.mark.asyncio
async def test_corrupted_interval_refuses_with_or_without_a_valid_tail(
    tmp_path: Path, good_tail: bool
) -> None:
    """Real SQLite corruption inside the observed interval cannot disappear behind a good tail."""
    path = tmp_path / "authority.db"
    with EventStore(path) as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)

        async def collect() -> dict[str, bytes]:
            """Corrupt a real SQLite journal row during collection, optionally followed by a valid
            tail.
            """
            corrupt = journal.append("chat", {"message": "will be corrupt"})
            if good_tail:
                journal.append("chat", {"message": "good tail"})
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE events SET payload=? WHERE seq=?", ("invalid-json", corrupt)
                )
                connection.commit()
            return {"record": b"must not be returned"}

        with pytest.raises(ValueError, match="corrupt"):
            await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)


@pytest.mark.parametrize("fault", ["lost-admission", "cycle", "lost-custody", "history-budget"])
@pytest.mark.asyncio
async def test_incomplete_history_refuses_until_durable_authority_is_rebuilt(
    tmp_path: Path, fault: str
) -> None:
    """Refuse damaged live history, then read only after actual durable replay.

    The same collector must remain unentered while the authority is inconsistent
    and read the real file after rebuilding from the unchanged durable history.
    Pending custody also requires an authenticated no-write revoke before the
    rebuilt authority is idle; reconstructing a hub alone cannot release it.
    """
    record = tmp_path / "record"
    record.write_bytes(b"durably recovered read")
    with record.open("rb") as captured, _committed(tmp_path) as journal:
        hub = SynapseHub(
            journal=journal, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        grant = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({"revoke"}))
        registry = {grant.session_id: grant}
        request = _authenticated_transition(
            "revoke", grant=grant, key=AUTHOR_KEY, nonce="history-fault", registry=registry
        )
        if fault != "lost-custody":
            await _handler(hub, registry)(request)
        limits = LIMITS
        if fault == "lost-admission":
            hub.state.protected_write_admissions.clear()
        elif fault == "cycle":
            hub.state.protected_write_recoveries["reservation"] = "reservation"
        elif fault == "lost-custody":
            hub.state.protected_claim_custody.clear()
        else:
            hub.state.protected_write_reservations["unexpected"] = (
                hub.state.protected_write_reservations["reservation"]
            )
            limits = replace(LIMITS, max_reservations=1)
        entered = False

        async def collect() -> dict[str, bytes]:
            """Read the same actual file only when complete idle history admits collection."""
            nonlocal entered
            entered = True
            return {"record": captured.read()}

        messages = {
            "lost-admission": "incomplete reservation history",
            "cycle": "cyclic or unreachable recovery history",
            "lost-custody": "terminal custody mismatch",
            "history-budget": "reservation budget exceeded",
        }
        with pytest.raises(ValueError, match=messages[fault]):
            await read_at_protected_write_boundary(hub, limits=limits, collect=collect)
        assert not entered and captured.tell() == 0
        recovered = SynapseHub(
            journal=journal, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        if fault == "lost-custody":
            with pytest.raises(ValueError, match="outstanding write custody"):
                await read_at_protected_write_boundary(recovered, limits=LIMITS, collect=collect)
            assert not entered and captured.tell() == 0
            body = json.loads(await _handler(recovered, registry)(request))["body"]
            assert body["outcome"] == "no_write"
        result = await read_at_protected_write_boundary(recovered, limits=LIMITS, collect=collect)
        assert entered and result.content["record"] == b"durably recovered read"
        assert result.before_sequence == result.after_sequence == journal.max_seq()


@pytest.mark.asyncio
async def test_volatile_authority_and_mutable_captured_bytes_are_refused(tmp_path: Path) -> None:
    """Actual missing journal and mutable file-read buffers cannot supply a stable snapshot."""
    record = tmp_path / "record"
    record.write_bytes(b"a")
    buffer = bytearray(1)
    with record.open("rb") as handle:
        assert handle.readinto(buffer) == 1

    async def collect() -> dict[str, Any]:
        """Return the actual mutable buffer whose publication the read boundary must refuse."""
        return {"record": buffer}

    with pytest.raises(ValueError, match="complete authority journal"):
        await read_at_protected_write_boundary(SynapseHub(), limits=LIMITS, collect=collect)
    with EventStore(tmp_path / "authority.db") as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)
        with pytest.raises(ValueError, match="immutable named bytes"):
            await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)


@pytest.mark.parametrize("invalid", ["sequence", "bytes"])
def test_public_snapshot_record_rejects_invalid_range_or_mutable_input(invalid: str) -> None:
    """A manually supplied public data record must preserve its typed immutable contract."""
    message = "sequence range" if invalid == "sequence" else "immutable named bytes"
    with pytest.raises(ValueError, match=message):
        if invalid == "sequence":
            ProtectedReadSnapshot({"record": b"a"}, 2, 1)
        else:
            malformed: Any = {"record": bytearray(b"a")}
            ProtectedReadSnapshot(malformed, 1, 1)


@pytest.mark.parametrize("budget", ["entries", "empty", "bytes", "traffic"])
@pytest.mark.asyncio
async def test_actual_content_and_journal_traffic_respect_enrolled_bounds(
    tmp_path: Path, budget: str
) -> None:
    """The real collection boundary rejects aggregate bytes, entry count and journal overflow."""
    with EventStore(tmp_path / "authority.db") as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)
        limits = replace(LIMITS, max_files=1, max_bytes=3, max_intervening_events=1)

        async def collect() -> dict[str, bytes]:
            """Produce the selected real content-size or journal-interval budget violation."""
            if budget == "empty":
                return {}
            if budget == "entries":
                return {"a": b"a", "b": b"b"}
            if budget == "bytes":
                return {"a": b"four"}
            journal.append("chat", {"message": "one"})
            journal.append("chat", {"message": "two"})
            return {"a": b"a"}

        with pytest.raises(ValueError, match="budget exceeded"):
            await read_at_protected_write_boundary(hub, limits=limits, collect=collect)


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.asyncio
async def test_read_completion_or_deadline_settles_the_actual_collector_once(
    tmp_path: Path, complete: bool
) -> None:
    """A ready collector publishes once; a stalled one is cancelled without retry.

    Both paths use the same actual asynchronous collector and finalization event,
    so successful completion cannot be confused with a timeout's cleanup.
    """
    calls = 0
    settled, release = asyncio.Event(), asyncio.Event()
    if complete:
        release.set()
    with EventStore(tmp_path / "authority.db") as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)

        async def collect() -> dict[str, bytes]:
            """Wait for a real release event and always report the collector's settlement."""
            nonlocal calls
            calls += 1
            try:
                await release.wait()
                return {"record": b"completed before deadline"}
            finally:
                settled.set()

        if complete:
            result = await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)
            assert result.content["record"] == b"completed before deadline"
        else:
            with pytest.raises(TimeoutError):
                await read_at_protected_write_boundary(
                    hub, limits=replace(LIMITS, timeout_seconds=0.05), collect=collect
                )
        assert calls == 1 and settled.is_set()


@pytest.mark.asyncio
async def test_blocking_sqlite_collection_cannot_publish_after_its_deadline(tmp_path: Path) -> None:
    """An actual source lock outlasting the read budget must not publish late fallback bytes."""
    source = tmp_path / "source.db"
    with closing(sqlite3.connect(source)) as owner:
        owner.execute("CREATE TABLE records (value TEXT)")
        owner.commit()
        owner.execute("BEGIN EXCLUSIVE")
        with EventStore(tmp_path / "authority.db") as journal:
            hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)

            async def collect() -> dict[str, bytes]:
                """Wait on a real SQLite lock past the deadline and return bytes that must not
                publish.
                """
                with closing(sqlite3.connect(source, timeout=0.04)) as reader:
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        reader.execute("SELECT * FROM records").fetchall()
                return {"record": b"late collector bytes"}

            with pytest.raises(TimeoutError):
                await read_at_protected_write_boundary(
                    hub, limits=replace(LIMITS, timeout_seconds=0.005), collect=collect
                )


@pytest.mark.parametrize("invalid", ["zero", "boolean", "nonfinite"])
def test_public_read_limits_refuse_disabled_or_ambiguous_budgets(invalid: str) -> None:
    """Actual constructor refuses missing budgets, boolean integers and infinite deadlines."""
    with pytest.raises(ValueError, match="invalid protected read"):
        if invalid == "zero":
            replace(LIMITS, max_bytes=0)
        elif invalid == "boolean":
            replace(LIMITS, max_files=True)
        else:
            replace(LIMITS, timeout_seconds=float("inf"))


@pytest.mark.asyncio
async def test_ordered_read_uses_pinned_root_after_actual_external_path_substitution(
    tmp_path: Path,
) -> None:
    """A lookalike external root cannot redirect a real actor-ordered descriptor read."""
    enrolled = tmp_path / "enrolled"
    enrolled.mkdir(mode=0o700)
    (enrolled / "record").write_bytes(b"enrolled content")
    (enrolled / "record").chmod(0o600)
    root = os.open(enrolled, os.O_RDONLY | os.O_DIRECTORY)
    try:
        info = os.fstat(root)
        expected = inspect_protected_file(
            root, "record", root_identity=(info.st_dev, info.st_ino), max_bytes=64
        )
        with (
            EventStore(tmp_path / "authority.db") as journal,
            hold_protected_path(
                root,
                expected,
                owner_uid=os.getuid(),
                max_bytes=64,
                require_openat2=sys.platform == "linux",
            ) as held,
        ):
            hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)

            async def collect() -> dict[str, bytes]:
                """Replace the real pathname while reading the originally retained object
                descriptor.
                """
                enrolled.rename(tmp_path / "retained")
                enrolled.mkdir(mode=0o700)
                (enrolled / "record").write_bytes(b"substituted content")
                return {"record": held.read_bytes()}

            result = await read_at_protected_write_boundary(
                hub,
                limits=ProtectedReadLimits(4, 1, 64, 4, 2.0),
                collect=collect,
            )
            assert result.content["record"] == b"enrolled content"
            assert (enrolled / "record").read_bytes() == b"substituted content"
            assert journal.read_all() == []
    finally:
        os.close(root)


@pytest.mark.asyncio
async def test_erased_idle_reservation_history_cannot_validate_collected_content(
    tmp_path: Path,
) -> None:
    """Clearing live history after a real no-write revoke cannot validate the old boundary."""
    with _committed(tmp_path) as journal:
        hub = SynapseHub(
            journal=journal, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        grant = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({"revoke"}))
        registry = {grant.session_id: grant}
        request = _authenticated_transition(
            "revoke", grant=grant, key=AUTHOR_KEY, nonce="erase-history", registry=registry
        )
        await _handler(hub, registry)(request)
        assert hub.state.protected_claim_custody == {}

        async def collect() -> dict[str, bytes]:
            """Erase actual in-memory history while retaining the unchanged original journal."""
            hub.state.protected_write_reservations.clear()
            hub.state.protected_write_admissions.clear()
            hub.state.protected_write_recoveries.clear()
            return {"record": b"must not publish"}

        with pytest.raises(ValueError, match="retained reservation history changed"):
            await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)


@pytest.mark.asyncio
async def test_deleted_protected_interval_rows_cannot_hide_behind_valid_chat_tail(
    tmp_path: Path,
) -> None:
    """Physical deletion of new enrollment events leaves a refused journal gap."""
    path = tmp_path / "authority.db"
    with EventStore(path) as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={"session": KEY}, clock=lambda: NOW
        )

        async def collect() -> dict[str, bytes]:
            """Issue/revoke through the control API, then corrupt only this test database."""
            await control.issue(enrollment())
            await control.revoke("session")
            journal.append("chat", {"message": "valid tail"})
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("DELETE FROM events WHERE kind LIKE 'protected_%'")
                connection.commit()
            return {"record": b"must not publish"}

        with pytest.raises(ValueError, match="journal interval has missing events"):
            await read_at_protected_write_boundary(hub, limits=LIMITS, collect=collect)


@pytest.mark.parametrize("label", ["record", "\u00e9"])
@pytest.mark.parametrize("excess", [False, True])
@pytest.mark.asyncio
async def test_content_budget_counts_encoded_labels_and_payload(
    tmp_path: Path, label: str, excess: bool
) -> None:
    """UTF-8 label bytes and payload together consume the actual aggregate budget."""
    with EventStore(tmp_path / "authority.db") as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)
        limits = replace(LIMITS, max_bytes=len(label.encode("utf-8")) + 1 - int(excess))

        async def collect() -> dict[str, bytes]:
            """Return one immutable entry exactly at or one byte beyond the chosen bound."""
            return {label: b"x"}

        if excess:
            with pytest.raises(ValueError, match="aggregate byte budget exceeded"):
                await read_at_protected_write_boundary(hub, limits=limits, collect=collect)
        else:
            snapshot = await read_at_protected_write_boundary(hub, limits=limits, collect=collect)
            assert snapshot.content[label] == b"x"

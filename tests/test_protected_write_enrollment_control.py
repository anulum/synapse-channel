# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actor-ordered protected enrollment persistence
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import MessageReplayCache, sign_frame
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_enrollment_journal import enrollment_issue_payload
from synapse_channel.core.protected_write_session_auth import (
    authenticate_protected_request,
    recheck_authenticated_protected_request,
)
from synapse_channel.core.state import SynapseState
from test_protected_write_enrollment_journal import _enrollment
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request
from test_protected_write_session_auth import enrollment


@pytest.mark.asyncio
async def test_issue_revoke_and_restart_use_one_real_hub_actor(tmp_path: Path) -> None:
    path = tmp_path / "actor-enroll.db"
    first = _enrollment()
    second = _enrollment("writer-2", "incarnation-2", b"b" * 32)
    keys = {first.session_id: first.key, second.session_id: second.key}
    with EventStore(path) as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys=keys, clock=lambda: 1_900_000_000.0
        )
        await control.issue(first)
        assert control.current() == {first.session_id: first}
        with pytest.raises(ValueError, match="active incarnation"):
            await control.issue(second)
        await control.revoke(first.session_id)
        assert control.current() == {}
        await control.issue(second)
        assert control.current() == {second.session_id: second}
    with EventStore(path) as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys=keys, clock=lambda: 1_900_000_000.0
        )
        assert control.current() == {second.session_id: second}
        with pytest.raises(ValueError, match="session id reused"):
            await control.issue(first)
        control = ProtectedEnrollmentControl(
            hub=hub,
            sealed_keys={**keys, "writer-3": first.key},
            clock=lambda: 1_900_000_000.0,
        )
        with pytest.raises(ValueError, match="key id reused"):
            await control.issue(replace(first, session_id="writer-3"))


@pytest.mark.asyncio
async def test_two_writer_starts_serialize_and_one_is_refused(tmp_path: Path) -> None:
    first = _enrollment()
    second = _enrollment("writer-2", "incarnation-2", b"b" * 32)
    with EventStore(tmp_path / "race.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub,
            sealed_keys={first.session_id: first.key, second.session_id: second.key},
            clock=lambda: 1_900_000_000.0,
        )
        results = await asyncio.gather(
            control.issue(first), control.issue(second), return_exceptions=True
        )
        assert sum(result is None for result in results) == 1
        assert sum(isinstance(result, ValueError) for result in results) == 1
        assert len(control.current()) == 1


@pytest.mark.asyncio
async def test_failed_real_append_never_publishes_issued_session(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "closed.db")
    hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
    first = _enrollment()
    control = ProtectedEnrollmentControl(
        hub=hub, sealed_keys={first.session_id: first.key}, clock=lambda: 1_900_000_000.0
    )
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        await control.issue(first)
    assert hub.state.protected_enrollments == {}
    assert control.current() == {}


@pytest.mark.asyncio
async def test_missing_or_wrong_sealed_key_fails_closed_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "keys.db"
    first = _enrollment()
    with EventStore(path) as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={first.session_id: first.key}, clock=lambda: 1_900_000_000.0
        )
        await control.issue(first)
    with EventStore(path) as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        missing = ProtectedEnrollmentControl(hub=hub, sealed_keys={}, clock=lambda: 1_900_000_000.0)
        assert missing.current() == {}
        with pytest.raises(ValueError, match="sealed operator key"):
            await missing.issue(_enrollment("writer-2", "incarnation-2", b"b" * 32))
        wrong = ProtectedEnrollmentControl(
            hub=hub,
            sealed_keys={first.session_id: replace(first.key, secret=b"z" * 32)},
            clock=lambda: 1_900_000_000.0,
        )
        assert wrong.current() == {}
        with pytest.raises(ValueError, match="exact session id"):
            await wrong.revoke("")


@pytest.mark.asyncio
async def test_expired_enrollment_cannot_be_issued_or_projected(tmp_path: Path) -> None:
    first = _enrollment()
    with EventStore(tmp_path / "expiry.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={first.session_id: first.key}, clock=lambda: first.expires_at
        )
        with pytest.raises(ValueError, match="expired"):
            await control.issue(first)
        assert control.current() == {}


def test_volatile_hub_cannot_own_enrollment_control() -> None:
    with pytest.raises(ValueError, match="complete durable journal"):
        ProtectedEnrollmentControl(hub=SynapseHub(), sealed_keys={}, clock=lambda: 1.0)


@pytest.mark.asyncio
async def test_issue_rechecks_expiry_under_actor_before_durable_append(tmp_path: Path) -> None:
    first = _enrollment()
    readings = iter((first.expires_at - 1, first.expires_at))
    with EventStore(tmp_path / "clock-boundary.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={first.session_id: first.key}, clock=lambda: next(readings)
        )
        with pytest.raises(ValueError, match="expired"):
            await control.issue(first)
        assert hub.state.protected_enrollments == {}
        assert list(store.iter_events()) == []


@pytest.mark.asyncio
async def test_invalid_authority_clock_refuses_projection_and_issue(tmp_path: Path) -> None:
    first = _enrollment()
    with EventStore(tmp_path / "invalid-clock.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={first.session_id: first.key}, clock=lambda: float("nan")
        )
        with pytest.raises(ValueError, match="clock unavailable"):
            control.current()
        with pytest.raises(ValueError, match="clock unavailable"):
            await control.issue(first)


@pytest.mark.asyncio
async def test_operator_metadata_uses_only_presealed_key(tmp_path: Path) -> None:
    first = _enrollment()
    payload = enrollment_issue_payload(first)
    metadata = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "key_fingerprint"}
    }
    with EventStore(tmp_path / "metadata.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={first.session_id: first.key}, clock=lambda: 1_900_000_000.0
        )
        for change, error in (
            ({"unexpected": True}, "fields mismatch"),
            ({"session_id": ""}, "session id invalid"),
            ({"session_id": "unknown"}, "sealed key unavailable"),
            ({"key_id": "wrong"}, "sealed key unavailable"),
            ({"verbs": []}, "verbs invalid"),
            ({"verbs": ["settle", "begin"]}, "sorted and unique"),
            ({"principal": ""}, "nonempty identifiers"),
        ):
            with pytest.raises(ValueError, match=error):
                await control.issue_from_metadata({**metadata, **change})
        await control.issue_from_metadata(metadata)
        assert control.current() == {first.session_id: first}


async def test_real_clock_issue_restart_signed_request_and_durable_revocation(
    tmp_path: Path,
) -> None:
    """Actual metadata replay remains coupled to current signed-request admission."""
    now = time.time()
    grant = replace(enrollment(), expires_at=now + 60)
    path = tmp_path / "signed-session.db"
    keys = {grant.session_id: grant.key}
    payload = enrollment_issue_payload(grant)
    metadata = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "key_fingerprint"}
    }
    with EventStore(path) as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(hub=hub, sealed_keys=keys, clock=time.time)
        await control.issue_from_metadata(metadata)
        assert control.current() == {grant.session_id: grant}
    assert grant.key.secret not in path.read_bytes()
    with EventStore(path) as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(hub=hub, sealed_keys=keys, clock=time.time)
        assert control.current() == {grant.session_id: grant}
        signed = sign_frame(
            _request("begin"), key=grant.key, nonce="real-clock", sequence=1, timestamp=time.time()
        )
        authenticated = authenticate_protected_request(
            json.dumps(signed),
            limits=LIMITS,
            enrollments=control.current(),
            authenticated_principal=grant.principal,
            replay_cache=MessageReplayCache(window_seconds=30, max_entries=32),
            now=time.time(),
        )
        assert authenticated.enrollment == grant
        await control.revoke(grant.session_id)
        assert control.current() == {}
        with pytest.raises(ValueError, match="unavailable"):
            recheck_authenticated_protected_request(
                authenticated,
                enrollments=control.current(),
                authenticated_principal=grant.principal,
                now=time.time(),
            )
        assert [event.kind for event in store.read_all()] == [
            "protected_write_enrollment_issue",
            "protected_write_enrollment_revoke",
        ]
    with EventStore(path) as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(hub=hub, sealed_keys=keys, clock=time.time)
        assert control.current() == {}
        assert hub.state.protected_enrollments[grant.session_id].revoked is True


@pytest.mark.parametrize("operation", ["issue", "revoke"])
async def test_cancellation_waits_for_the_actual_contended_enrollment_commit(
    tmp_path: Path, operation: str
) -> None:
    """Native SQLite contention keeps the provisional enrollment out of live state."""
    grant = _enrollment()
    path = tmp_path / "contended.db"
    with EventStore(path) as store, sqlite3.connect(path, isolation_level=None) as blocker:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={grant.session_id: grant.key}, clock=time.time
        )
        if operation == "revoke":
            await control.issue(grant)
        before = control.current()
        entered = threading.Event()

        def trace(sql: str) -> None:
            if sql.strip().upper().startswith("BEGIN"):
                entered.set()

        store.storage.connection.set_trace_callback(trace)
        blocker.execute("BEGIN IMMEDIATE")
        task = asyncio.create_task(
            control.issue(grant) if operation == "issue" else control.revoke(grant.session_id)
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert control.current() == before
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            blocker.rollback()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        store.storage.connection.set_trace_callback(None)
        expected = {grant.session_id: grant} if operation == "issue" else {}
        assert control.current() == expected
        assert store.count() == (1 if operation == "issue" else 2)
    with EventStore(path) as store:
        restarted = SynapseHub(
            journal=store, protected_write_policies={}, anti_rollback_checkpoint=False
        )
        control = ProtectedEnrollmentControl(
            hub=restarted, sealed_keys={grant.session_id: grant.key}, clock=time.time
        )
        assert control.current() == expected


async def test_claim_renewal_and_refused_publication_preserve_enrollment(
    tmp_path: Path,
) -> None:
    """Adding enrollment registries preserves existing state ownership boundaries."""
    grant = _enrollment()
    with EventStore(tmp_path / "shared-state.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={grant.session_id: grant.key}, clock=time.time
        )
        await control.issue(grant)
        state = hub.state
        identity = ClaimScopeIdentity(
            worktree_path=str(tmp_path),
            case_sensitive=True,
            paths=(CanonicalPathIdentity("declared", "declared"),),
        )
        accepted, reason = state.claim(
            "owner", "mismatch", worktree=str(tmp_path), paths=["other"], path_identity=identity
        )
        assert not accepted and "path identity" in reason
        assert state.claim("owner", "first", worktree=str(tmp_path), paths=["shared"])[0]
        original = state.claims["first"]
        assert not state.renew_claim("foreign", "first", epoch=original.epoch, paths=("shared",))[0]
        assert not state.renew_claim("owner", "first", epoch=original.epoch, paths=("other",))[0]
        assert state.renew_claim("owner", "first", epoch=original.epoch, paths=("shared",))[0]
        assert state.claim("owner", "second", worktree=str(tmp_path), paths=["shared"])[0]
        accepted, reason = state.handoff("owner", "first", "recipient")
        assert not accepted and "scope conflicts" in reason
        with pytest.raises(ValueError, match="configuration does not match"):
            state.publish_from(SynapseState(default_ttl_seconds=state.default_ttl_seconds + 1))
        state.publish_from(deepcopy(state))
        assert control.current() == {grant.session_id: grant}

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a token set's check and the writes it causes stay off the hub's serving loop
"""An authentication reads one token set; the file read and the writes run in worker threads."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from websockets.asyncio.client import connect

import synapse_channel.core.deferred_writes as deferred_writes_module
import synapse_channel.core.token_set as token_set_module
from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_ingress import HubIngress
from synapse_channel.core.metrics_token_watch import metrics_token_watch_for
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.core.token_audit import (
    AUDIT_OVERFLOW_KIND,
    METRICS_AUDIT_OVERFLOW_KIND,
    METRICS_EXPIRED_KIND,
    METRICS_SET_REJECTED_KIND,
    SET_APPLIED_KIND,
    SET_REJECTED_KIND,
    metrics_token_audit_for,
    token_audit_for,
)
from synapse_channel.core.token_sessions import check_token_set
from synapse_channel.core.token_set import TokenSetAuthenticator, connect_token_digest
from synapse_channel.token_set_hub_status import publish_hub_state

_SLOW = 1.0
"""How long a slowed read or write takes, in seconds."""

_PROMPT = 0.3
"""The longest an authentication, a connection or the loop may wait while a slowed step runs."""

_BASE = 1_800_000_000.0
"""A fixed instant on the validity clock."""


def _write(path: Path, *entries: tuple[str, str], not_after: str | None = None) -> Path:
    """Replace the owner-only token-set file atomically with ``(id, secret)`` entries."""
    tokens: list[dict[str, Any]] = []
    for token_id, secret in entries:
        entry: dict[str, Any] = {"id": token_id, "sha256": connect_token_digest(secret)}
        if not_after is not None:
            entry["not_after"] = not_after
        tokens.append(entry)
    staging = path.with_name(path.name + ".new")
    staging.write_text(json.dumps({"version": 1, "tokens": tokens}), encoding="utf-8")
    apply_owner_only_file(staging)
    os.replace(staging, path)
    return path


def _rows(store: EventStore, kind: str) -> list[dict[str, Any]]:
    return [dict(event.payload) for event in store.iter_events(kinds=(kind,))]


@pytest.fixture
def store(tmp_path: Path) -> Any:
    journal = EventStore(tmp_path / "hub.db")
    yield journal
    journal.close()


class _SlowOnce:
    """Wraps a callable: once armed, its next call records when and where it began, then sleeps."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.armed = threading.Event()
        self.began = threading.Event()
        self.began_at = 0.0
        self.thread = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.armed.is_set() and not self.began.is_set():
            self.began_at = time.monotonic()
            self.thread = threading.get_ident()
            self.began.set()
            time.sleep(_SLOW)
        return self.inner(*args, **kwargs)


def test_an_authentication_reads_the_set_in_force_and_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replaced file changes nothing until a check puts it in force."""
    path = _write(tmp_path / "tokens.json", ("a", "secret-a"))
    authenticator = TokenSetAuthenticator(path)
    _write(path, ("b", "secret-b"))
    file_status = mock.Mock(wraps=token_set_module._signature)
    monkeypatch.setattr(token_set_module, "_signature", file_status)
    assert authenticator.authenticate_outcome("secret-a", "A").ok
    assert authenticator.authenticate_outcome("secret-b", "A").code == "unknown"
    file_status.assert_not_called()
    monkeypatch.undo()
    authenticator.refresh()
    assert authenticator.authenticate_outcome("secret-b", "A").ok
    assert authenticator.authenticate_outcome("secret-a", "A").code == "unknown"


async def test_a_connection_during_a_slow_token_file_read_is_answered_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the hub's check reads a replaced file, a connection is admitted by the set in force."""
    path = _write(tmp_path / "tokens.json", ("a", "secret-a"))
    journal = EventStore(tmp_path / "hub.db")
    authenticator = TokenSetAuthenticator(path)
    read = _SlowOnce(authenticator.prepare)
    monkeypatch.setattr(authenticator, "prepare", read)
    try:
        async with running_hub(SynapseHub(journal=journal, authenticator=authenticator)) as (
            _,
            uri,
        ):
            _write(path, ("a", "secret-a"), ("b", "secret-b"))
            read.armed.set()
            await asyncio.to_thread(read.began.wait, 5)
            assert time.monotonic() - read.began_at < _PROMPT  # the loop was free during the read
            assert read.thread != threading.get_ident()
            started = time.monotonic()
            async with connect(uri) as websocket:
                await send_json(websocket, sender="A", type="heartbeat", token="secret-a")
                await read_until_type(websocket, "welcome")
            assert time.monotonic() - started < _PROMPT
            deadline = time.monotonic() + 10
            while ["a", "b"] not in [row["token_ids"] for row in _rows(journal, SET_APPLIED_KIND)]:
                assert time.monotonic() < deadline, "the replaced file was never applied"
                await asyncio.sleep(0.05)
            async with connect(uri) as websocket:
                await send_json(websocket, sender="B", type="heartbeat", token="secret-b")
                await read_until_type(websocket, "welcome")
    finally:
        journal.close()


async def test_a_slow_journal_never_holds_an_authentication_or_the_loop(
    tmp_path: Path, store: EventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row of an applied set is queued where the set changes and written in a worker thread."""
    path = _write(tmp_path / "tokens.json", ("a", "secret-a"))
    authenticator = TokenSetAuthenticator(path)
    assert token_audit_for(authenticator, store) is not None
    slowed = _SlowOnce(store.append)
    monkeypatch.setattr(store, "append", slowed)
    _write(path, ("a", "secret-a"), ("b", "secret-b"))
    slowed.armed.set()
    check = asyncio.create_task(check_token_set(authenticator))
    await asyncio.to_thread(slowed.began.wait, 5)
    assert time.monotonic() - slowed.began_at < _PROMPT  # the loop was free during the write
    assert slowed.thread != threading.get_ident()
    started = time.monotonic()
    assert authenticator.authenticate_outcome("secret-b", "A").ok  # in force before its row
    assert time.monotonic() - started < _PROMPT
    await check
    assert [row["token_ids"] for row in _rows(store, SET_APPLIED_KIND)] == [["a"], ["a", "b"]]


async def test_the_hub_state_file_is_written_off_the_loop(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json", ("a", "secret-a"))
    authenticator = TokenSetAuthenticator(path)
    writer = publish_hub_state(authenticator)
    assert writer is not None
    try:
        first = json.loads(writer.path.read_text(encoding="utf-8"))
        writes = _SlowOnce(writer._write)
        writes.armed.set()
        with mock.patch.object(writer, "_write", writes):
            replace = path.with_name("other.json")
            replace.write_text("{}", encoding="utf-8")
            apply_owner_only_file(replace)
            os.replace(replace, path)
            check = asyncio.create_task(check_token_set(authenticator))
            await asyncio.to_thread(writes.began.wait, 5)
            assert writes.thread != threading.get_ident()
            assert time.monotonic() - writes.began_at < _PROMPT
            await check
        later = json.loads(writer.path.read_text(encoding="utf-8"))
    finally:
        writer.close()
    assert (first["state"], later["state"], later["reason_code"]) == ("applied", "stale", "invalid")


@pytest.mark.parametrize(
    ("attach", "rejected_kind", "overflow_kind"),
    [
        (token_audit_for, SET_REJECTED_KIND, AUDIT_OVERFLOW_KIND),
        (metrics_token_audit_for, METRICS_SET_REJECTED_KIND, METRICS_AUDIT_OVERFLOW_KIND),
    ],
)
def test_a_row_that_cannot_be_written_stays_first_and_a_full_queue_is_counted(
    tmp_path: Path,
    store: EventStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    attach: Any,
    rejected_kind: str,
    overflow_kind: str,
) -> None:
    """The connect and the metrics token set queue their rows by the same rules."""
    path = _write(tmp_path / "tokens.json", ("a", "secret-a"))
    authenticator = TokenSetAuthenticator(path)
    assert attach(authenticator, store) is not None
    monkeypatch.setattr(deferred_writes_module, "AUDIT_QUEUE_LIMIT", 2)
    real_append = store.append
    journal_down = [True]

    def append_unless_down(*args: Any, **kwargs: Any) -> Any:
        if journal_down[0]:
            raise OSError("journal unavailable")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(store, "append", append_unless_down)
    caplog.set_level(logging.ERROR, logger="synapse_channel.core.token_set")
    for content in ("{}", "[]", "{}"):
        replace = path.with_name("next.json")
        replace.write_text(content, encoding="utf-8")
        apply_owner_only_file(replace)
        os.replace(replace, path)
        authenticator.refresh()  # a failed write is logged and stays queued
    full = [
        record
        for record in caplog.records
        if "audit of tokens.json queue is full" in record.getMessage()
    ]
    assert len(full) == 1
    assert sum("stays queued" in record.getMessage() for record in caplog.records) == 3
    journal_down[0] = False
    authenticator.flush()
    authenticator.flush()
    assert len(_rows(store, rejected_kind)) == 2
    assert _rows(store, overflow_kind) == [{"dropped": 1}]


def test_the_hub_writes_the_token_set_queue_once_more_when_it_stops_serving(
    tmp_path: Path, store: EventStore, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(tmp_path / "tokens.json", ("a", "secret-a"))
    authenticator = TokenSetAuthenticator(path)
    assert token_audit_for(authenticator, store) is not None
    _write(path, ("a", "secret-a"), ("b", "secret-b"))
    authenticator.apply(authenticator.prepare())  # put in force; its row is queued
    assert [row["token_ids"] for row in _rows(store, SET_APPLIED_KIND)] == [["a"]]
    broken = mock.Mock()  # a secret watch whose last write fails
    broken.flush.side_effect = OSError("cannot write")
    ingress = HubIngress(
        HubClientRegistry(
            max_clients=8,
            max_unauth_clients=None,
            max_connections_per_host=None,
            takeover_cooldown=0.0,
            clock=lambda: 0.0,
        ),
        authenticator=authenticator,
        enable_metrics=False,
        metrics_token=None,
        metrics_query_token_ok=False,
        insecure_off_loopback=False,
        send_json=mock.AsyncMock(),
        system=lambda payload, **extra: {"payload": payload, **extra},
        secret_watches=(broken,),
    )
    caplog.set_level(logging.ERROR, logger="synapse_channel.core.hub_ingress")
    ingress.flush_audit()
    assert [row["token_ids"] for row in _rows(store, SET_APPLIED_KIND)] == [["a"], ["a", "b"]]
    assert any("last rows of a rotating secret" in record.getMessage() for record in caplog.records)
    _write(path, ("b", "secret-b"))
    authenticator.apply(authenticator.prepare())
    with mock.patch.object(store, "append", side_effect=OSError("journal unavailable")):
        ingress.flush_audit()  # a failed last write is logged; the hub still stops
    assert any("last rows of the connect token set" in r.getMessage() for r in caplog.records)


async def test_the_metrics_check_reads_and_writes_off_the_loop(
    tmp_path: Path, store: EventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The metrics watch reads its file and writes its rows in worker threads."""
    path = _write(
        tmp_path / "metrics.json", ("old", "old-secret"), not_after="2027-01-15T08:00:00Z"
    )
    token_set = TokenSetAuthenticator(path, clock=mock.Mock(return_value=_BASE))
    watch = metrics_token_watch_for(token_set, store)
    assert watch is not None
    read = _SlowOnce(token_set.prepare)
    monkeypatch.setattr(token_set, "prepare", read)
    written = _SlowOnce(store.append)
    monkeypatch.setattr(store, "append", written)
    read.armed.set()
    written.armed.set()
    tick = asyncio.create_task(watch.tick())
    await asyncio.to_thread(read.began.wait, 5)
    assert read.thread != threading.get_ident()
    assert time.monotonic() - read.began_at < _PROMPT
    await asyncio.to_thread(written.began.wait, 5)
    assert written.thread != threading.get_ident()
    assert time.monotonic() - written.began_at < _PROMPT
    await tick
    rows = _rows(store, METRICS_EXPIRED_KIND)
    assert [(row["token_id"], row["observed"]) for row in rows] == [("old", "watcher")]


def test_a_metrics_request_queues_its_expiry_row_and_the_check_writes_it(
    tmp_path: Path, store: EventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request that sees an expired metrics token writes nothing; the next check writes it."""
    path = _write(
        tmp_path / "metrics.json", ("old", "old-secret"), not_after="2027-01-15T08:00:00Z"
    )
    clock = mock.Mock(return_value=_BASE)
    token_set = TokenSetAuthenticator(path, clock=clock)
    watch = metrics_token_watch_for(token_set, store)
    assert watch is not None
    appended = mock.Mock(wraps=store.append)
    monkeypatch.setattr(store, "append", appended)
    assert token_set.authenticate_outcome("old-secret", "").code == "expired"
    appended.assert_not_called()
    watch.check()
    rows = _rows(store, METRICS_EXPIRED_KIND)
    assert [(row["token_id"], row["observed"]) for row in rows] == [("old", "request")]
    assert _rows(store, METRICS_AUDIT_OVERFLOW_KIND) == []

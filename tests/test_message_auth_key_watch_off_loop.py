# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a key set's check and its audit rows stay off the hub's serving loop
"""A verification reads one snapshot; file reads and journal writes run in worker threads."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

import synapse_channel.core.deferred_writes as deferred_writes_module
import synapse_channel.core.message_auth_key_set as key_set_module
from key_set_helpers import instant, key_entry, replace_owner_only, write_key_set, write_secret
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_ingress import HubIngress
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.message_auth_key_set import MessageAuthKeySet
from synapse_channel.core.message_auth_key_watch import (
    MESSAGE_KEY_AUDIT_OVERFLOW_KIND,
    MESSAGE_KEY_EXPIRED_KIND,
    MESSAGE_KEY_RETIRING_ADMISSION_KIND,
    MESSAGE_KEY_SET_REJECTED_KIND,
    MessageAuthKeyWatch,
    message_auth_key_watch_for,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.token_set import status_digest

_BASE = 1_800_000_000.0
"""A fixed instant on the validity clock."""

_SLOW = 1.0
"""How long a slowed read or write takes, in seconds."""

_PROMPT = 0.2
"""The longest a verification or the loop may wait while a slowed step runs."""


class _Clock:
    """A validity clock the test moves by hand."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _set(tmp_path: Path, *entries: dict[str, Any], clock: _Clock) -> MessageAuthKeySet:
    """Write the secrets ``secret-<key id>`` and a set of ``entries``, then load it."""
    for entry in entries:
        write_secret(tmp_path, entry["key_id"], f"secret-{entry['key_id']}")
    path = tmp_path / "keys.json"
    write_key_set(path, *entries)
    return MessageAuthKeySet(path, clock=clock)


def _signed(key_id: str, secret: str, sender: str, nonce: str) -> dict[str, Any]:
    key = MessageAuthKey(key_id=key_id, secret=secret.encode(), senders=frozenset({sender}))
    frame = {"sender": sender, "type": "claim", "task_id": "T1", "idem_key": nonce}
    return sign_frame(frame, key=key, nonce=nonce, sequence=1, timestamp=_BASE)


def _verify(
    keys: MessageAuthKeySet, frame: dict[str, Any], cache: MessageReplayCache
) -> VerificationResult:
    return verify_frame(
        frame, keys=keys, replay_cache=cache, now=_BASE, required_sender=frame["sender"]
    )


def _cache() -> MessageReplayCache:
    return MessageReplayCache(window_seconds=300.0, max_entries=100)


def _rows(store: EventStore, kind: str) -> list[dict[str, Any]]:
    return [dict(event.payload) for event in store.iter_events(kinds=(kind,))]


@pytest.fixture
def store(tmp_path: Path) -> Any:
    journal = EventStore(tmp_path / "hub.db")
    yield journal
    journal.close()


class _Slowed:
    """Wraps a callable: each call records when it began in its thread, then sleeps.

    Each test calls it once, from the worker thread whose hold on the loop it measures.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.began = threading.Event()
        self.began_at = 0.0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.began_at = time.monotonic()
        self.began.set()
        time.sleep(_SLOW)
        return self.inner(*args, **kwargs)


def test_a_lookup_reads_the_snapshot_and_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replaced secret file changes nothing until a check puts it in force."""
    keys = _set(tmp_path, key_entry("a", ["P/a"]), clock=_Clock(_BASE))
    write_secret(tmp_path, "a", "fresh-secret")
    file_status = mock.Mock(wraps=status_digest)
    monkeypatch.setattr(key_set_module, "status_digest", file_status)
    cache = _cache()
    assert _verify(keys, _signed("a", "secret-a", "P/a", "old"), cache) is VerificationResult.OK
    fresh = _signed("a", "fresh-secret", "P/a", "early")
    assert _verify(keys, fresh, cache) is VerificationResult.BAD_AUTHENTICATION
    assert list(keys) == ["a"] and len(keys) == 1
    file_status.assert_not_called()
    monkeypatch.undo()
    keys.refresh()
    later = _signed("a", "fresh-secret", "P/a", "later")
    assert _verify(keys, later, cache) is VerificationResult.OK


async def test_a_slow_read_never_holds_a_verification_or_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the check reads the files, the old snapshot answers at once."""
    keys = _set(tmp_path, key_entry("a", ["P/a"]), clock=_Clock(_BASE))
    watch = message_auth_key_watch_for(keys, None)
    assert watch is not None
    write_secret(tmp_path, "a", "fresh-secret")
    slowed = _Slowed(keys.prepare)
    monkeypatch.setattr(keys, "prepare", slowed)
    cache = _cache()
    tick = asyncio.create_task(watch.tick())
    await asyncio.to_thread(slowed.began.wait, 5)
    assert time.monotonic() - slowed.began_at < _PROMPT  # the loop was free during the read
    started = time.monotonic()
    old = _signed("a", "secret-a", "P/a", "during")
    assert _verify(keys, old, cache) is VerificationResult.OK
    assert time.monotonic() - started < _PROMPT
    await tick
    fresh = _signed("a", "fresh-secret", "P/a", "after")
    assert _verify(keys, fresh, cache) is VerificationResult.OK


async def test_a_slow_journal_never_holds_a_verification_or_the_loop(
    tmp_path: Path, store: EventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expiry seen at a verification is queued; the check writes it in a worker thread."""
    keys = _set(
        tmp_path,
        key_entry("old", ["P/a"], not_after=instant(_BASE - 1)),
        key_entry("a", ["P/a"]),
        clock=_Clock(_BASE),
    )
    watch = message_auth_key_watch_for(keys, store)
    assert watch is not None
    slowed = _Slowed(store.append)
    monkeypatch.setattr(store, "append", slowed)
    cache = _cache()
    started = time.monotonic()
    expired = _signed("old", "secret-old", "P/a", "expired")
    assert _verify(keys, expired, cache) is VerificationResult.EXPIRED
    assert time.monotonic() - started < _PROMPT and not slowed.began.is_set()
    tick = asyncio.create_task(watch.tick())
    await asyncio.to_thread(slowed.began.wait, 5)
    assert time.monotonic() - slowed.began_at < _PROMPT  # the loop was free during the write
    started = time.monotonic()
    assert _verify(keys, _signed("a", "secret-a", "P/a", "during"), cache) is VerificationResult.OK
    assert time.monotonic() - started < _PROMPT
    await tick
    assert _rows(store, MESSAGE_KEY_EXPIRED_KIND) == [
        {"key_id": "old", "not_after": _BASE - 1, "observed": "verification"}
    ]


def test_a_row_that_cannot_be_written_stays_first_in_the_queue(
    tmp_path: Path, store: EventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock(_BASE)
    keys = _set(
        tmp_path,
        key_entry("first", ["P/a"], not_after=instant(_BASE - 1)),
        key_entry("second", ["P/b"], not_after=instant(_BASE + 10)),
        clock=clock,
    )
    watch = message_auth_key_watch_for(keys, store)
    assert watch is not None
    real_append = store.append
    failures = [OSError("journal unavailable")]

    def append_after_one_failure(*args: Any, **kwargs: Any) -> Any:
        if failures:
            raise failures.pop()
        return real_append(*args, **kwargs)

    monkeypatch.setattr(store, "append", append_after_one_failure)
    with pytest.raises(OSError, match="journal unavailable"):
        watch.check()
    assert _rows(store, MESSAGE_KEY_EXPIRED_KIND) == []
    clock.now = _BASE + 20
    assert "second" in keys  # a verification sees the second expiry and queues it
    watch.flush()
    assert _rows(store, MESSAGE_KEY_EXPIRED_KIND) == [
        {"key_id": "first", "not_after": _BASE - 1, "observed": "watcher"},
        {"key_id": "second", "not_after": _BASE + 10, "observed": "verification"},
    ]


def test_a_full_queue_counts_what_it_cannot_keep_and_journals_the_count(
    tmp_path: Path,
    store: EventStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ended = [key_entry(f"k{n}", ["P/a"], not_after=instant(_BASE - 1)) for n in range(5)]
    keys = _set(tmp_path, *ended, clock=_Clock(_BASE))
    watch = message_auth_key_watch_for(keys, store)
    assert watch is not None
    monkeypatch.setattr(deferred_writes_module, "AUDIT_QUEUE_LIMIT", 2)
    real_append = store.append
    journal_down = [True]

    def append_unless_down(*args: Any, **kwargs: Any) -> Any:
        if journal_down[0]:
            raise OSError("journal unavailable")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(store, "append", append_unless_down)
    caplog.set_level(logging.ERROR, logger="synapse_channel.core.message_auth_key_watch")
    with pytest.raises(OSError):
        watch.check()
    full = [record for record in caplog.records if "audit queue is full" in record.getMessage()]
    assert len(full) == 1
    journal_down[0] = False
    watch.flush()
    watch.flush()
    assert [row["key_id"] for row in _rows(store, MESSAGE_KEY_EXPIRED_KIND)] == ["k0", "k1"]
    assert _rows(store, MESSAGE_KEY_AUDIT_OVERFLOW_KIND) == [{"dropped": 3}]


def test_a_rejected_file_is_journalled_through_the_queue(tmp_path: Path, store: EventStore) -> None:
    keys = _set(tmp_path, key_entry("a", ["P/a"]), clock=_Clock(_BASE))
    watch = message_auth_key_watch_for(keys, store)
    assert watch is not None
    replace_owner_only(keys.path, "{}")
    keys.refresh()
    assert _rows(store, MESSAGE_KEY_SET_REJECTED_KIND) == []
    watch.flush()
    rows = _rows(store, MESSAGE_KEY_SET_REJECTED_KIND)
    assert len(rows) == 1 and "version and keys" in rows[0]["reason"]


def test_the_hub_writes_the_queue_once_more_when_it_stops_serving(
    tmp_path: Path, store: EventStore, caplog: pytest.LogCaptureFixture
) -> None:
    clock = _Clock(_BASE + 10)
    keys = _set(
        tmp_path,
        key_entry("old-a", ["P/a"], not_before=instant(_BASE - 7200)),
        key_entry("new-a", ["P/a"], not_before=instant(_BASE - 3600)),
        clock=clock,
    )
    watch = message_auth_key_watch_for(keys, store)
    assert isinstance(watch, MessageAuthKeyWatch)
    keys.admitted("old-a", "P/a")
    assert _rows(store, MESSAGE_KEY_RETIRING_ADMISSION_KIND) == []
    broken = mock.Mock()  # another secret watch, whose last write fails
    broken.flush.side_effect = OSError("cannot write")
    ingress = HubIngress(
        HubClientRegistry(
            max_clients=8,
            max_unauth_clients=None,
            max_connections_per_host=None,
            takeover_cooldown=0.0,
            clock=lambda: 0.0,
        ),
        authenticator=None,
        enable_metrics=False,
        metrics_token=None,
        metrics_query_token_ok=False,
        insecure_off_loopback=False,
        send_json=mock.AsyncMock(),
        system=lambda payload, **extra: {"payload": payload, **extra},
        secret_watches=(broken, watch),
    )
    caplog.set_level(logging.ERROR, logger="synapse_channel.core.hub_ingress")
    ingress.flush_audit()
    assert _rows(store, MESSAGE_KEY_RETIRING_ADMISSION_KIND) == [
        {"key_id": "old-a", "sender": "P/a", "newest_key_id": "new-a", "hour_start": _BASE}
    ]
    assert any("last rows of a rotating secret" in record.getMessage() for record in caplog.records)

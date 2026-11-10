# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a per-message key set rotates, expires and journals through public verification
"""A per-message key set validates, reloads, expires keys and keeps replays refused."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from key_set_helpers import instant, key_entry, replace_owner_only, write_key_set, write_secret
from synapse_channel.core.hourly_once import HOUR_SECONDS, HourlyOnce
from synapse_channel.core.journal import EventKind
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.message_auth_key_set import (
    MessageAuthKeyEntry,
    MessageAuthKeySet,
    MessageAuthKeySetError,
    message_auth_key_configured,
    parse_message_auth_key_set,
)
from synapse_channel.core.message_auth_key_watch import (
    MESSAGE_KEY_EXPIRED_KIND,
    MESSAGE_KEY_RETIRING_ADMISSION_KIND,
    MESSAGE_KEY_SET_APPLIED_KIND,
    MESSAGE_KEY_SET_REJECTED_KIND,
    RETIRING_MEMORY_HOURS,
    message_auth_key_watch_for,
    newest_for_sender,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.record_keeping import OPERATION_RECORD, RISK_INDICATOR, purpose_for_kind
from synapse_channel.core.secret_files import SecretFileError
from synapse_channel.core.universal_receipts import NON_RECEIPT_EVENT_KINDS

_BASE = 1_800_000_000.0
"""A fixed instant on the validity clock, at the start of an hour."""


class _Clock:
    """A validity clock the test moves by hand."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _set(
    tmp_path: Path, *entries: dict[str, Any], clock: _Clock | None = None
) -> MessageAuthKeySet:
    """Write the secrets ``secret-<key id>`` and a set of ``entries``, then load it."""
    for entry in entries:
        write_secret(tmp_path, entry["key_id"], f"secret-{entry['key_id']}")
    path = tmp_path / "keys.json"
    write_key_set(path, *entries)
    return MessageAuthKeySet(path) if clock is None else MessageAuthKeySet(path, clock=clock)


def _signed(key_id: str, secret: str, sender: str, *, nonce: str, at: float) -> dict[str, Any]:
    """Sign a claim frame from ``sender`` with ``key_id``/``secret`` at ``at``."""
    key = MessageAuthKey(key_id=key_id, secret=secret.encode(), senders=frozenset({sender}))
    frame = {"sender": sender, "type": "claim", "task_id": "T1", "idem_key": nonce}
    return sign_frame(frame, key=key, nonce=nonce, sequence=1, timestamp=at)


def _verify(
    keys: MessageAuthKeySet, frame: dict[str, Any], cache: MessageReplayCache, at: float
) -> VerificationResult:
    """Verify ``frame`` against the live set the way the hub's frame gate does."""
    return verify_frame(
        frame, keys=keys, replay_cache=cache, now=at, required_sender=frame["sender"]
    )


def _cache() -> MessageReplayCache:
    return MessageReplayCache(window_seconds=300.0, max_entries=100)


def _rows(store: EventStore, *kinds: str) -> list[tuple[str, dict[str, Any]]]:
    return [(event.kind, dict(event.payload)) for event in store.iter_events(kinds=kinds)]


@pytest.fixture
def store(tmp_path: Path) -> Any:
    journal = EventStore(tmp_path / "hub.db")
    yield journal
    journal.close()


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("{not json", "not valid JSON"),
        ('{"version": 1}', "exactly version and keys"),
        ('{"version": 2, "keys": []}', "version must be 1"),
        ('{"version": true, "keys": []}', "version must be 1"),
        ('{"version": 1, "keys": {}}', "keys must be a list"),
        ('{"version": 1, "keys": ["x"]}', "key #0: an entry must be an object"),
        ('{"version": 1, "keys": [{"key_id": "a", "extra": 1}]}', "unknown fields"),
        ('{"version": 1, "keys": [{"key_id": "a b"}]}', "key_id must match"),
        ('{"version": 1, "keys": [{"key_id": "a", "secret_file": ""}]}', "secret_file must"),
        (
            '{"version": 1, "keys": [{"key_id": "a", "secret_file": "a.key", "senders": []}]}',
            "senders must be a non-empty list",
        ),
        (
            '{"version": 1, "keys": [{"key_id": "a", "secret_file": "a.key", "senders": [""]}]}',
            "senders must be a non-empty list",
        ),
    ],
)
def test_a_malformed_set_is_refused_by_name(document: str, message: str) -> None:
    with pytest.raises(MessageAuthKeySetError, match=message):
        parse_message_auth_key_set(document, base=Path("/keys"))


def test_windows_and_duplicates_are_validated() -> None:
    def document(**extra: Any) -> str:
        entry = {"key_id": "a", "secret_file": "a.key", "senders": ["P/a"], **extra}
        return json.dumps({"version": 1, "keys": [entry]})

    with pytest.raises(MessageAuthKeySetError, match="key a: not_before must be"):
        parse_message_auth_key_set(document(not_before="tomorrow"), base=Path("/k"))
    with pytest.raises(MessageAuthKeySetError, match="key a: not_after is not a real date"):
        parse_message_auth_key_set(document(not_after="2026-02-30T00:00:00Z"), base=Path("/k"))
    with pytest.raises(MessageAuthKeySetError, match="later than not_before"):
        parse_message_auth_key_set(
            document(not_before=instant(_BASE), not_after=instant(_BASE)), base=Path("/k")
        )
    twice = json.dumps({"version": 1, "keys": [json.loads(document())["keys"][0]] * 2})
    with pytest.raises(MessageAuthKeySetError, match="key ids must be unique"):
        parse_message_auth_key_set(twice, base=Path("/k"))
    absolute = json.dumps(
        {"version": 1, "keys": [{"key_id": "a", "secret_file": "/abs/a.key", "senders": ["P/a"]}]}
    )
    (entry,) = parse_message_auth_key_set(absolute, base=Path("/k"))
    assert entry == MessageAuthKeyEntry("a", Path("/abs/a.key"), frozenset({"P/a"}), None, None)
    (relative,) = parse_message_auth_key_set(document(), base=Path("/k"))
    assert relative.secret_file == Path("/k/a.key")


def test_an_unreadable_secret_refuses_the_initial_set(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    write_key_set(path, key_entry("a", ["P/a"]))
    with pytest.raises(SecretFileError, match="key a"):
        MessageAuthKeySet(path)
    replace_owner_only(tmp_path / "a.key", "\n")
    with pytest.raises(SecretFileError, match="is empty"):
        MessageAuthKeySet(path)


def test_keys_follow_their_window_through_verification(tmp_path: Path) -> None:
    clock = _Clock(_BASE)
    keys = _set(
        tmp_path,
        key_entry("now", ["P/a"], not_after=instant(_BASE + 60)),
        key_entry("later", ["P/a"], not_before=instant(_BASE + 30)),
        clock=clock,
    )
    assert keys.path == tmp_path / "keys.json" and keys.stale_reason is None
    assert list(keys) == ["now"] and len(keys) == 1 and "later" not in keys
    cache = _cache()
    early = _signed("later", "secret-later", "P/a", nonce="n1", at=_BASE)
    assert _verify(keys, early, cache, _BASE) is VerificationResult.UNKNOWN_KEY
    clock.now = _BASE + 61
    assert sorted(keys) == ["later", "now"]
    late_frame = _signed("later", "secret-later", "P/a", nonce="n2", at=clock.now)
    assert _verify(keys, late_frame, cache, clock.now) is VerificationResult.OK
    expired = _signed("now", "secret-now", "P/a", nonce="n3", at=clock.now)
    assert _verify(keys, expired, cache, clock.now) is VerificationResult.EXPIRED
    assert keys["now"].not_after == _BASE + 60


def test_replacing_only_a_secret_file_rotates_that_key(tmp_path: Path) -> None:
    """The set file's bytes never change; the secret file alone decides the key."""
    keys = _set(tmp_path, key_entry("a", ["P/a"]))
    manifest = (tmp_path / "keys.json").read_bytes()
    cache = _cache()
    write_secret(tmp_path, "a", "fresh-secret")
    old = _signed("a", "secret-a", "P/a", nonce="old", at=_BASE)
    new = _signed("a", "fresh-secret", "P/a", nonce="new", at=_BASE)
    assert _verify(keys, old, cache, _BASE) is VerificationResult.BAD_AUTHENTICATION
    assert _verify(keys, new, cache, _BASE) is VerificationResult.OK
    replace_owner_only(tmp_path / "a.key", "\n")  # empty: not a usable secret
    still = _signed("a", "fresh-secret", "P/a", nonce="still", at=_BASE)
    assert _verify(keys, still, cache, _BASE) is VerificationResult.OK
    assert keys.stale_reason is not None and "a.key" in keys.stale_reason
    write_secret(tmp_path, "a", "repaired-secret")
    repaired = _signed("a", "repaired-secret", "P/a", nonce="repaired", at=_BASE)
    assert _verify(keys, repaired, cache, _BASE) is VerificationResult.OK
    assert keys.stale_reason is None
    assert (tmp_path / "keys.json").read_bytes() == manifest


def test_a_broken_set_file_keeps_the_last_set_and_a_new_secret_file_is_watched(
    tmp_path: Path,
) -> None:
    keys = _set(tmp_path, key_entry("a", ["P/a"]))
    replace_owner_only(tmp_path / "keys.json", "{not json")
    assert list(keys) == ["a"] and "not valid JSON" in str(keys.stale_reason)
    write_key_set(tmp_path / "keys.json", key_entry("a", ["P/a"]), key_entry("b", ["P/b"]))
    assert list(keys) == ["a"] and "key b" in str(keys.stale_reason)  # b.key is missing
    write_secret(tmp_path, "b", "secret-b")
    assert list(keys) == ["a", "b"] and keys.stale_reason is None


def test_a_removed_and_readded_key_never_readmits_a_replay(tmp_path: Path) -> None:
    keys = _set(tmp_path, key_entry("a", ["P/a"]), key_entry("b", ["P/b"]))
    cache = _cache()
    frame = _signed("a", "secret-a", "P/a", nonce="once", at=_BASE)
    assert _verify(keys, frame, cache, _BASE) is VerificationResult.OK
    write_key_set(tmp_path / "keys.json", key_entry("b", ["P/b"]))
    assert _verify(keys, frame, cache, _BASE) is VerificationResult.UNKNOWN_KEY
    write_key_set(tmp_path / "keys.json", key_entry("a", ["P/a"]), key_entry("b", ["P/b"]))
    assert _verify(keys, frame, cache, _BASE) is VerificationResult.REPLAYED


def test_observers_hear_changes_and_their_failures_change_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    keys = _set(tmp_path, key_entry("a", ["P/a"]))
    heard: list[tuple[str, object]] = []

    class Broken:
        def set_applied(self, path: Path, key_ids: tuple[str, ...]) -> None:
            if heard:
                raise RuntimeError("down")
            heard.append(("applied", key_ids))

        def set_rejected(self, path: Path, reason: str) -> None:
            raise RuntimeError("down")

    def broken_hook(*_args: object) -> None:
        raise RuntimeError("down")

    keys.observe(Broken())
    keys.observe_lookups(broken_hook)
    keys.observe_admissions(broken_hook)
    caplog.set_level(logging.ERROR, logger="synapse_channel.core.message_auth_key_set")
    write_key_set(tmp_path / "keys.json", key_entry("a", ["P/a"]), key_entry("a2", ["P/a"]))
    write_secret(tmp_path, "a2", "secret-a2")
    assert "a" in keys
    replace_owner_only(tmp_path / "keys.json", "[]")
    keys.refresh()
    keys.admitted("a", "P/a")
    keys.admitted("unknown", "P/a")
    assert heard == [("applied", ("a",))]
    messages = [record.getMessage() for record in caplog.records]
    for fragment in ("applied set", "rejected set", "lookup observer", "admission observer"):
        assert any(fragment in message for message in messages), fragment
    late_dir = tmp_path / "late"
    late_dir.mkdir()
    late = _set(late_dir, key_entry("c", ["P/c"]))
    replace_owner_only(late.path, "{}")
    late.refresh()
    seen: list[tuple[str, object]] = []

    class Recorder:
        def set_applied(self, path: Path, key_ids: tuple[str, ...]) -> None:
            seen.append(("applied", key_ids))

        def set_rejected(self, path: Path, reason: str) -> None:
            seen.append(("rejected", reason))

    late.observe(Recorder())
    assert seen[0] == ("applied", ("c",)) and seen[1][0] == "rejected"


def test_configuration_counts_a_set_or_static_keys() -> None:
    assert message_auth_key_configured(argparse.Namespace(message_auth_key_set="keys.json"))
    assert message_auth_key_configured(argparse.Namespace(message_auth_key=["k:s:P/a"]))
    assert not message_auth_key_configured(argparse.Namespace(message_auth_key=[]))


def test_only_a_key_set_gets_a_watch(tmp_path: Path, store: EventStore) -> None:
    keys = _set(tmp_path, key_entry("a", ["P/a"]))
    assert message_auth_key_watch_for(None, store) is None
    assert message_auth_key_watch_for([], store) is None
    assert message_auth_key_watch_for({}, store) is None
    assert message_auth_key_watch_for(keys, None) is not None
    assert _rows(store, MESSAGE_KEY_SET_APPLIED_KIND) == []
    assert message_auth_key_watch_for(keys, store) is not None
    assert _rows(store, MESSAGE_KEY_SET_APPLIED_KIND) == [
        (MESSAGE_KEY_SET_APPLIED_KIND, {"path": str(keys.path), "key_ids": ["a"]})
    ]


def test_expiry_is_one_row_whichever_check_sees_it_first(tmp_path: Path, store: EventStore) -> None:
    clock = _Clock(_BASE)
    keys = _set(
        tmp_path,
        key_entry("seen-by-check", ["P/a"], not_after=instant(_BASE + 10)),
        key_entry("seen-at-use", ["P/b"], not_after=instant(_BASE + 20)),
        key_entry("open", ["P/c"]),
        clock=clock,
    )
    watch = message_auth_key_watch_for(keys, store)
    assert watch is not None
    watch.check()
    clock.now = _BASE + 15
    watch.check()
    watch.check()
    clock.now = _BASE + 25
    frame = _signed("seen-at-use", "secret-seen-at-use", "P/b", nonce="n", at=clock.now)
    assert _verify(keys, frame, _cache(), clock.now) is VerificationResult.EXPIRED
    watch.check()
    assert _rows(store, MESSAGE_KEY_EXPIRED_KIND) == [
        (
            MESSAGE_KEY_EXPIRED_KIND,
            {"key_id": "seen-by-check", "not_after": _BASE + 10, "observed": "watcher"},
        ),
        (
            MESSAGE_KEY_EXPIRED_KIND,
            {"key_id": "seen-at-use", "not_after": _BASE + 20, "observed": "verification"},
        ),
    ]
    assert "secret-" not in json.dumps(_rows(store, MESSAGE_KEY_EXPIRED_KIND))


def test_a_retiring_key_is_judged_per_sender_and_hour(tmp_path: Path, store: EventStore) -> None:
    """A newer key for another sender replaces nothing; each key and sender rows once an hour."""
    clock = _Clock(_BASE + 10)
    keys = _set(
        tmp_path,
        key_entry("old-a", ["P/a"], not_before=instant(_BASE - 3 * 3600)),
        key_entry("new-a", ["P/a"], not_before=instant(_BASE - 2 * 3600)),
        key_entry("b", ["P/b"], not_before=instant(_BASE - 3600)),
        key_entry("shared", ["P/a", "P/c"], not_before=instant(_BASE - 4 * 3600)),
        clock=clock,
    )
    assert message_auth_key_watch_for(keys, store) is not None
    for _ in range(2):
        keys.admitted("old-a", "P/a")
        keys.admitted("new-a", "P/a")
        keys.admitted("b", "P/b")
        keys.admitted("shared", "P/c")
    keys.admitted("shared", "P/a")
    clock.now = _BASE + HOUR_SECONDS + 10
    keys.admitted("old-a", "P/a")
    rows = [payload for _, payload in _rows(store, MESSAGE_KEY_RETIRING_ADMISSION_KIND)]
    assert rows == [
        {"key_id": "old-a", "sender": "P/a", "newest_key_id": "new-a", "hour_start": _BASE},
        {"key_id": "shared", "sender": "P/a", "newest_key_id": "new-a", "hour_start": _BASE},
        {
            "key_id": "old-a",
            "sender": "P/a",
            "newest_key_id": "new-a",
            "hour_start": _BASE + HOUR_SECONDS,
        },
    ]


def test_without_a_journal_the_files_are_still_checked(tmp_path: Path) -> None:
    keys = _set(
        tmp_path, key_entry("a", ["P/a"], not_after=instant(_BASE - 1)), clock=_Clock(_BASE)
    )
    watch = message_auth_key_watch_for(keys, None)
    assert watch is not None
    write_key_set(tmp_path / "keys.json", key_entry("a", ["P/a"]), key_entry("b", ["P/b"]))
    write_secret(tmp_path, "b", "secret-b")
    watch.check()
    keys.admitted("a", "P/a")
    assert [entry.key_id for entry in keys.entries] == ["a", "b"]


def test_the_newest_key_for_a_sender_orders_open_starts_and_ties() -> None:
    def entry(key_id: str, senders: set[str], not_before: float | None, **extra: Any) -> Any:
        return MessageAuthKeyEntry(
            key_id, Path(key_id), frozenset(senders), not_before, extra.get("not_after")
        )

    open_a = entry("open-a", {"P/a"}, None)
    dated_a = entry("dated-a", {"P/a"}, _BASE - 10)
    twin_1 = entry("twin-1", {"P/a"}, _BASE - 5)
    twin_2 = entry("twin-2", {"P/a"}, _BASE - 5)
    other = entry("other", {"P/b"}, _BASE - 1)
    ended = entry("ended", {"P/a"}, _BASE - 2, not_after=_BASE)
    pending = entry("pending", {"P/a"}, _BASE + 5)
    assert newest_for_sender((dated_a, open_a), "P/a", _BASE) == dated_a
    assert newest_for_sender((twin_2, twin_1, other, ended, pending), "P/a", _BASE) == twin_1
    assert newest_for_sender((twin_1, twin_2), "P/a", _BASE) == twin_2
    assert newest_for_sender((other,), "P/a", _BASE) is None


def test_the_hourly_memory_is_bounded_and_survives_a_step_back() -> None:
    once = HourlyOnce(memory_hours=2)
    assert once.is_new("s", 10)
    once.mark("s", 10)
    assert not once.is_new("s", 10)
    assert once.is_new("s", 9)  # unrecorded and inside the memory
    assert not once.is_new("s", 8)  # as far back as the memory reaches
    once.mark("s", 13)
    assert not once.is_new("s", 11) and once.is_new("s", 12)
    assert RETIRING_MEMORY_HOURS == 24


class _FailureCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.count = 0
        self.second = asyncio.Event()

    def emit(self, record: logging.LogRecord) -> None:
        self.count += "key-set files" in record.getMessage()
        if self.count >= 2:
            self.second.set()


async def test_a_failed_check_is_logged_and_the_loop_goes_on(tmp_path: Path) -> None:
    keys = _set(
        tmp_path, key_entry("a", ["P/a"], not_after=instant(_BASE - 1)), clock=_Clock(_BASE)
    )
    journal = EventStore(tmp_path / "closed.db")
    watch = message_auth_key_watch_for(keys, journal)
    assert watch is not None
    journal.close()
    counter = _FailureCounter()
    watch_logger = logging.getLogger("synapse_channel.core.message_auth_key_watch")
    watch_logger.addHandler(counter)
    loop = asyncio.create_task(watch.run(interval=0.01))
    try:
        await asyncio.wait_for(counter.second.wait(), timeout=10)
        assert not loop.done()
    finally:
        watch_logger.removeHandler(counter)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)


def test_the_kinds_are_registered_like_the_other_secret_set_kinds() -> None:
    assert EventKind.MESSAGE_AUTH_KEY_SET_APPLIED == MESSAGE_KEY_SET_APPLIED_KIND
    assert EventKind.MESSAGE_AUTH_KEY_SET_REJECTED == MESSAGE_KEY_SET_REJECTED_KIND
    assert EventKind.MESSAGE_AUTH_KEY_EXPIRED == MESSAGE_KEY_EXPIRED_KIND
    assert EventKind.MESSAGE_AUTH_KEY_RETIRING_ADMISSION == MESSAGE_KEY_RETIRING_ADMISSION_KIND
    assert purpose_for_kind(MESSAGE_KEY_SET_APPLIED_KIND) == OPERATION_RECORD
    assert purpose_for_kind(MESSAGE_KEY_EXPIRED_KIND) == OPERATION_RECORD
    assert purpose_for_kind(MESSAGE_KEY_SET_REJECTED_KIND) == RISK_INDICATOR
    assert purpose_for_kind(MESSAGE_KEY_RETIRING_ADMISSION_KIND) == RISK_INDICATOR
    assert {
        EventKind.MESSAGE_AUTH_KEY_SET_APPLIED,
        EventKind.MESSAGE_AUTH_KEY_SET_REJECTED,
        EventKind.MESSAGE_AUTH_KEY_EXPIRED,
        EventKind.MESSAGE_AUTH_KEY_RETIRING_ADMISSION,
    } <= NON_RECEIPT_EVENT_KINDS


def test_the_durable_ledger_decides_while_its_memory_mirror_stays_bounded(tmp_path: Path) -> None:
    """A durable replay ledger admits past a full memory mirror and re-admits after its window.

    The mirror drops its oldest entry instead of growing; a nonce the ledger has let go
    is admitted again by the ledger and kept, moved to the end, in the mirror.
    """
    from synapse_channel.core.message_auth_durable import DurableMessageAuthReplayStore

    ledger = DurableMessageAuthReplayStore(
        tmp_path / "replay.db", max_entries=10, window_seconds=1.0
    )
    cache = MessageReplayCache(window_seconds=300.0, max_entries=1, durable=ledger)
    try:
        assert cache.admit("k", "P/a", "first", 1, timestamp=_BASE, now=_BASE) is None
        assert cache.admit("k", "P/a", "second", 2, timestamp=_BASE, now=_BASE) is None
        assert cache.admit("k", "P/a", "second", 3, timestamp=_BASE, now=_BASE) is (
            VerificationResult.REPLAYED
        )
        later = _BASE + 5
        assert cache.admit("k", "P/a", "second", 4, timestamp=later, now=later) is None
    finally:
        ledger.close()


def test_message_auth_imports_without_the_optional_security_extra() -> None:
    """Without ``cryptography`` the module imports and Ed25519 verification refuses."""
    script = """import json, sys
sys.modules["cryptography"] = None
from synapse_channel.core import message_auth
key = message_auth.EventSignatureKey(key_id="k", public_key=bytes(32))
try:
    key.verifier()
except ImportError:
    refused = True
else:
    refused = False
print(json.dumps({"available": message_auth.CRYPTOGRAPHY_AVAILABLE, "refused": refused}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"available": False, "refused": True}

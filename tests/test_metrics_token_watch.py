# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a metrics token set is checked periodically; expiries and overlaps journaled
"""Periodic check, expiry rows and retiring-entry admissions of a metrics token set."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from hub_e2e_helpers import http_get, running_hub
from synapse_channel.core import hub_http
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.metrics_token_watch import (
    HOUR_SECONDS,
    RETIRING_MEMORY_HOURS,
    MetricsTokenWatch,
    metrics_token_watch_for,
    newest_valid,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.record_keeping import OPERATION_RECORD, RISK_INDICATOR, purpose_for_kind
from synapse_channel.core.token_audit import (
    METRICS_EXPIRED_KIND,
    METRICS_RETIRING_ADMISSION_KIND,
    METRICS_SET_APPLIED_KIND,
    SET_APPLIED_KIND,
)
from synapse_channel.core.token_sessions import WATCH_INTERVAL_SECONDS
from synapse_channel.core.token_set import TokenEntry, TokenSetAuthenticator, connect_token_digest
from synapse_channel.core.universal_receipts import NON_RECEIPT_EVENT_KINDS

_BASE = 1_800_000_000.0
"""A fixed instant on the validity clock, at the start of an hour."""


def _instant(seconds: float) -> str:
    """Format POSIX ``seconds`` the way a token-set file states an instant."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _entry(token_id: str, secret: str, **extra: Any) -> dict[str, Any]:
    """Return one token-set entry for ``secret``; the file keeps only its digest."""
    return {"id": token_id, "sha256": connect_token_digest(secret), **extra}


def _write(path: Path, *entries: dict[str, Any]) -> None:
    """Replace the owner-only token-set file atomically, so the change is seen."""
    staging = path.with_name(path.name + ".new")
    staging.write_text(json.dumps({"version": 1, "tokens": list(entries)}), "utf-8")
    staging.chmod(0o600)
    os.replace(staging, path)


class _Clock:
    """A validity clock the test moves by hand."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _admits(token_set: TokenAuthenticator, secret: str) -> bool:
    """Ask the hub's public metrics admission whether ``secret`` is let in."""
    headers = Headers()
    headers["Authorization"] = f"Bearer {secret}"
    return hub_http.metrics_authorised(
        Request("/metrics", headers),
        metrics_token=None,
        query_token_ok=False,
        metrics_token_set=token_set,
    )


def _rows(store: EventStore, *kinds: str) -> list[tuple[str, dict[str, Any]]]:
    """Read the rows of ``kinds`` in journal order."""
    return [(event.kind, dict(event.payload)) for event in store.iter_events(kinds=kinds)]


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A real hub journal, closed after the test."""
    journal = EventStore(tmp_path / "hub.db")
    yield journal
    journal.close()


def test_only_a_token_set_gets_a_watch(tmp_path: Path, store: EventStore) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(path, _entry("scraper", "secret"))
    assert metrics_token_watch_for(None, store) is None
    assert metrics_token_watch_for(TokenAuthenticator(["fixed"]), store) is None
    watch = metrics_token_watch_for(TokenSetAuthenticator(path), store)
    assert isinstance(watch, MetricsTokenWatch)


def test_without_a_journal_the_file_is_still_checked(tmp_path: Path) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(path, _entry("scraper-1", "first-secret", not_after=_instant(_BASE - 10)))
    clock = _Clock(_BASE)
    token_set = TokenSetAuthenticator(path, clock=clock)
    watch = metrics_token_watch_for(token_set, None)
    assert watch is not None
    _write(path, _entry("scraper-2", "second-secret-longer"))
    watch.check()
    assert [entry.token_id for entry in token_set.entries] == ["scraper-2"]
    assert _admits(token_set, "second-secret-longer")


def test_an_expiry_seen_at_a_request_is_one_row_never_repeated(
    tmp_path: Path, store: EventStore
) -> None:
    path = tmp_path / "metrics-tokens.json"
    end = _BASE + 30
    _write(path, _entry("scraper", "secret", not_after=_instant(end)), _entry("open", "open-key"))
    clock = _Clock(_BASE)
    token_set = TokenSetAuthenticator(path, clock=clock)
    watch = metrics_token_watch_for(token_set, store)
    assert watch is not None
    assert _admits(token_set, "secret")
    watch.check()
    assert _rows(store, METRICS_EXPIRED_KIND) == []

    clock.now = end + 5
    assert not _admits(token_set, "secret")
    assert _admits(token_set, "open-key")
    watch.check()
    clock.now = end + 3600
    watch.check()
    assert not _admits(token_set, "secret")
    assert _rows(store, METRICS_EXPIRED_KIND) == [
        (METRICS_EXPIRED_KIND, {"token_id": "scraper", "not_after": end, "observed": "request"})
    ]


def test_an_entry_already_expired_is_recorded_by_the_first_check(
    tmp_path: Path, store: EventStore
) -> None:
    path = tmp_path / "metrics-tokens.json"
    end = _BASE - 60
    _write(path, _entry("old", "old-secret", not_after=_instant(end)))
    token_set = TokenSetAuthenticator(path, clock=_Clock(_BASE))
    watch = metrics_token_watch_for(token_set, store)
    assert watch is not None
    watch.check()
    watch.check()
    assert _rows(store, METRICS_EXPIRED_KIND) == [
        (METRICS_EXPIRED_KIND, {"token_id": "old", "not_after": end, "observed": "watcher"})
    ]
    assert "old-secret" not in json.dumps(_rows(store, METRICS_EXPIRED_KIND))


def test_an_older_valid_entry_is_retiring_and_rows_once_per_hour(
    tmp_path: Path, store: EventStore
) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(
        path,
        _entry("older", "older-secret", not_before=_instant(_BASE - 7200)),
        _entry("newer", "newer-secret", not_before=_instant(_BASE - 3600)),
    )
    clock = _Clock(_BASE + 10)
    token_set = TokenSetAuthenticator(path, clock=clock)
    assert metrics_token_watch_for(token_set, store) is not None
    for _ in range(3):
        assert _admits(token_set, "older-secret")
        assert _admits(token_set, "newer-secret")
    clock.now = _BASE + HOUR_SECONDS + 10
    assert _admits(token_set, "older-secret")
    assert _admits(token_set, "older-secret")
    assert _rows(store, METRICS_RETIRING_ADMISSION_KIND) == [
        (
            METRICS_RETIRING_ADMISSION_KIND,
            {"token_id": "older", "newest_token_id": "newer", "hour_start": _BASE},
        ),
        (
            METRICS_RETIRING_ADMISSION_KIND,
            {"token_id": "older", "newest_token_id": "newer", "hour_start": _BASE + HOUR_SECONDS},
        ),
    ]


def test_no_newer_valid_entry_means_no_retiring_row(tmp_path: Path, store: EventStore) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(
        path,
        _entry("current", "current-secret", not_before=_instant(_BASE - 7200)),
        _entry("pending", "pending-secret", not_before=_instant(_BASE + 600)),
        _entry("bound", "bound-secret", not_before=_instant(_BASE - 60), agents=["SYNAPSE/a"]),
        _entry(
            "ended", "ended-secret", not_before=_instant(_BASE - 120), not_after=_instant(_BASE)
        ),
    )
    token_set = TokenSetAuthenticator(path, clock=_Clock(_BASE + 10))
    assert metrics_token_watch_for(token_set, store) is not None
    assert _admits(token_set, "current-secret")
    assert not _admits(token_set, "pending-secret")
    assert not _admits(token_set, "bound-secret")
    assert _rows(store, METRICS_RETIRING_ADMISSION_KIND) == []


def test_an_open_start_is_the_earliest_and_equal_starts_follow_the_file() -> None:
    def entry(token_id: str, not_before: float | None, **extra: Any) -> TokenEntry:
        return TokenEntry(token_id, "0" * 64, frozenset(), not_before, extra.get("not_after"))

    open_start = entry("open", None)
    dated = entry("dated", _BASE - 10)
    twin_a = entry("twin-a", _BASE - 5)
    twin_b = entry("twin-b", _BASE - 5)
    assert newest_valid((dated, open_start), _BASE) == dated
    open_later = entry("open-2", None)
    assert newest_valid((open_start, open_later), _BASE) == open_later
    assert newest_valid((twin_b, twin_a), _BASE) == twin_a
    assert newest_valid((twin_a, twin_b), _BASE) == twin_b
    assert newest_valid((entry("gone", None, not_after=_BASE),), _BASE) is None
    assert newest_valid((), _BASE) is None


def test_the_equal_start_earlier_in_the_file_is_the_retiring_one(
    tmp_path: Path, store: EventStore
) -> None:
    path = tmp_path / "metrics-tokens.json"
    start = _instant(_BASE - 60)
    _write(
        path,
        _entry("first", "first-secret", not_before=start),
        _entry("second", "second-secret", not_before=start),
    )
    token_set = TokenSetAuthenticator(path, clock=_Clock(_BASE + 10))
    assert metrics_token_watch_for(token_set, store) is not None
    assert _admits(token_set, "second-secret")
    assert _admits(token_set, "first-secret")
    rows = _rows(store, METRICS_RETIRING_ADMISSION_KIND)
    assert [payload["token_id"] for _, payload in rows] == ["first"]


def _retiring(store: EventStore) -> list[tuple[str, str, float]]:
    """Return each retiring-admission row as (entry, its newest replacement, hour start)."""
    return [
        (row["token_id"], row["newest_token_id"], row["hour_start"])
        for _, row in _rows(store, METRICS_RETIRING_ADMISSION_KIND)
    ]


def test_a_newer_entry_the_policy_refuses_is_no_replacement(
    tmp_path: Path, store: EventStore
) -> None:
    """With ``require_expiry`` an endless newer entry admits nothing, so the older is no retiree."""
    path = tmp_path / "metrics-tokens.json"
    _write(
        path,
        _entry(
            "older",
            "older-secret",
            not_before=_instant(_BASE - 7200),
            not_after=_instant(_BASE + 3600),
        ),
        _entry("endless", "endless-secret", not_before=_instant(_BASE - 3600)),
    )
    clock = _Clock(_BASE + 10)
    token_set = TokenSetAuthenticator(path, clock=clock, require_expiry=True)
    assert metrics_token_watch_for(token_set, store) is not None
    assert not _admits(token_set, "endless-secret")
    assert _admits(token_set, "older-secret")
    assert _retiring(store) == []

    _write(
        path,
        _entry(
            "older",
            "older-secret",
            not_before=_instant(_BASE - 7200),
            not_after=_instant(_BASE + 3600),
        ),
        _entry("endless", "endless-secret", not_before=_instant(_BASE - 3600)),
        _entry(
            "bounded",
            "bounded-secret",
            not_before=_instant(_BASE - 1800),
            not_after=_instant(_BASE + 7200),
        ),
    )
    assert _admits(token_set, "older-secret")
    assert _retiring(store) == [("older", "bounded", _BASE)]


def test_a_newer_entry_beyond_the_day_limit_is_no_replacement(
    tmp_path: Path, store: EventStore
) -> None:
    """With ``max_token_days`` a newer entry valid too long admits and replaces nothing."""
    path = tmp_path / "metrics-tokens.json"
    _write(
        path,
        _entry(
            "older",
            "older-secret",
            not_before=_instant(_BASE - 7200),
            not_after=_instant(_BASE + 3600),
        ),
        _entry(
            "long",
            "long-secret",
            not_before=_instant(_BASE - 3600),
            not_after=_instant(_BASE + 30 * 86_400),
        ),
    )
    token_set = TokenSetAuthenticator(path, clock=_Clock(_BASE + 10), max_token_days=1)
    assert metrics_token_watch_for(token_set, store) is not None
    assert not _admits(token_set, "long-secret")
    assert _admits(token_set, "older-secret")
    assert _retiring(store) == []


def test_newest_valid_applies_the_given_policy() -> None:
    older = TokenEntry("older", "0" * 64, frozenset(), _BASE - 20, None)
    newer = TokenEntry("newer", "1" * 64, frozenset(), _BASE - 10, None)

    def refuse_newer(entry: TokenEntry, _now: float) -> str | None:
        return "refused" if entry.token_id == "newer" else None

    assert newest_valid((older, newer), _BASE) == newer
    assert newest_valid((older, newer), _BASE, refuse_newer) == older


def test_a_clock_that_steps_back_writes_no_second_row_for_an_hour(
    tmp_path: Path, store: EventStore
) -> None:
    """Hours already recorded stay recorded; an unrecorded hour within the memory gets its row."""
    path = tmp_path / "metrics-tokens.json"
    _write(
        path,
        _entry("older", "older-secret", not_before=_instant(_BASE - 40 * 3600)),
        _entry("newer", "newer-secret", not_before=_instant(_BASE - 39 * 3600)),
    )
    clock = _Clock(_BASE + 3599.8)
    token_set = TokenSetAuthenticator(path, clock=clock)
    assert metrics_token_watch_for(token_set, store) is not None
    hour = HOUR_SECONDS
    steps = (
        3599.8,  # hour 0: a row
        3600.1,  # hour 1: a row
        3599.9,  # back to hour 0: already recorded
        -3 * hour + 10,  # hour -3, never recorded and within the memory: a row
        -(RETIRING_MEMORY_HOURS + 6) * hour,  # further back than the memory: no row
        (RETIRING_MEMORY_HOURS + 3) * hour + 5,  # far ahead: a row, older hours drop out
        hour + 20,  # hour 1 again, now behind the memory: no row
    )
    for offset in steps:
        clock.now = _BASE + offset
        assert _admits(token_set, "older-secret")
    assert _retiring(store) == [
        ("older", "newer", _BASE),
        ("older", "newer", _BASE + hour),
        ("older", "newer", _BASE - 3 * hour),
        ("older", "newer", _BASE + (RETIRING_MEMORY_HOURS + 3) * hour),
    ]


class _FailureCounter(logging.Handler):
    """Count the watch's logged check failures and signal after the second one."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.count = 0
        self.second = asyncio.Event()

    def emit(self, record: logging.LogRecord) -> None:
        self.count += "metrics token-set file" in record.getMessage()
        if self.count >= 2:
            self.second.set()


async def test_a_failed_check_is_logged_and_the_loop_goes_on(tmp_path: Path) -> None:
    """A journal that cannot be written fails each check; the loop logs it and keeps checking.

    The entry is marked recorded only after its row is written, so every check retries it.
    """
    path = tmp_path / "metrics-tokens.json"
    _write(path, _entry("old", "old-secret", not_after=_instant(_BASE - 60)))
    journal = EventStore(tmp_path / "closed.db")
    watch = metrics_token_watch_for(TokenSetAuthenticator(path, clock=_Clock(_BASE)), journal)
    assert watch is not None
    journal.close()
    counter = _FailureCounter()
    watch_logger = logging.getLogger("synapse_channel.core.metrics_token_watch")
    watch_logger.addHandler(counter)
    loop = asyncio.create_task(watch.run(interval=0.01))
    try:
        await asyncio.wait_for(counter.second.wait(), timeout=10)
        assert not loop.done()
    finally:
        watch_logger.removeHandler(counter)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)


def test_a_failing_outcome_observer_changes_no_verdict(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(path, _entry("scraper", "secret"))
    token_set = TokenSetAuthenticator(path)
    seen: list[str] = []

    def broken(_outcome: object) -> None:
        raise RuntimeError("observer down")

    token_set.observe_outcomes(broken)
    token_set.observe_outcomes(lambda outcome: seen.append(outcome.code))
    caplog.set_level(logging.ERROR, logger="synapse_channel.core.token_set")
    assert token_set.authenticate("secret", "")[0]
    assert not token_set.authenticate("wrong", "")[0]
    assert seen == ["admitted", "unknown"]
    assert sum("outcome observer failed" in r.getMessage() for r in caplog.records) == 2


def test_the_new_kinds_are_registered_like_the_other_metrics_kinds() -> None:
    assert EventKind.METRICS_TOKEN_EXPIRED == METRICS_EXPIRED_KIND
    assert EventKind.METRICS_TOKEN_RETIRING_ADMISSION == METRICS_RETIRING_ADMISSION_KIND
    assert purpose_for_kind(METRICS_EXPIRED_KIND) == OPERATION_RECORD
    assert purpose_for_kind(METRICS_RETIRING_ADMISSION_KIND) == RISK_INDICATOR
    assert {
        EventKind.METRICS_TOKEN_EXPIRED,
        EventKind.METRICS_TOKEN_RETIRING_ADMISSION,
    } <= NON_RECEIPT_EVENT_KINDS


async def test_a_live_hub_checks_both_token_sets_without_a_request(tmp_path: Path) -> None:
    """A hub with a connect and a metrics token set applies both files with no traffic."""
    connect = tmp_path / "connect-tokens.json"
    metrics = tmp_path / "metrics-tokens.json"
    _write(connect, _entry("agent-1", "agent-secret-one"))
    _write(metrics, _entry("scraper-1", "first-secret"))
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(
        hub_id="syn-metrics-watch",
        authenticator=TokenSetAuthenticator(connect),
        enable_metrics=True,
        metrics_token_set=TokenSetAuthenticator(metrics),
        journal=store,
    )
    try:
        async with running_hub(hub) as (_, uri):
            replaced = time.time()
            _write(connect, _entry("agent-2", "agent-secret-two"))
            _write(metrics, _entry("scraper-2", "second-secret-longer"))
            await asyncio.sleep(2 * WATCH_INTERVAL_SECONDS + 0.5)
            asked = time.time()
            status, _, _ = await http_get(uri, "/metrics", authorization="Bearer first-secret")
            assert status == 401
        applied = {
            (event.kind, tuple(event.payload["token_ids"])): event.ts
            for event in store.iter_events(kinds=(SET_APPLIED_KIND, METRICS_SET_APPLIED_KIND))
        }
    finally:
        store.close()
    for key in ((SET_APPLIED_KIND, ("agent-2",)), (METRICS_SET_APPLIED_KIND, ("scraper-2",))):
        assert replaced <= applied[key] <= replaced + 2 * WATCH_INTERVAL_SECONDS < asked, key

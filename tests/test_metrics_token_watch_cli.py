# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse hub --metrics-token-set` watches its set file in a real process
"""A real ``synapse hub`` checks its metrics token set without traffic and journals its life."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from cli_e2e_helpers import http_get, isolated_hub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.token_audit import (
    METRICS_EXPIRED_KIND,
    METRICS_RETIRING_ADMISSION_KIND,
    METRICS_SET_APPLIED_KIND,
    METRICS_SET_REJECTED_KIND,
)
from synapse_channel.core.token_sessions import WATCH_INTERVAL_SECONDS
from synapse_channel.core.token_set import connect_token_digest

_TWO_INTERVALS = 2 * WATCH_INTERVAL_SECONDS


def _entry(token_id: str, secret: str, **extra: Any) -> dict[str, Any]:
    """Return one token-set entry for ``secret``; the file keeps only its digest."""
    return {"id": token_id, "sha256": connect_token_digest(secret), **extra}


def _write(path: Path, *entries: dict[str, Any], raw: str | None = None) -> None:
    """Replace the owner-only set file atomically, so the hub sees a new file."""
    staging = path.with_name(path.name + ".new")
    staging.write_text(raw or json.dumps({"version": 1, "tokens": list(entries)}), "utf-8")
    staging.chmod(0o600)
    os.replace(staging, path)


def _instant(seconds: float) -> str:
    """Format POSIX ``seconds`` the way a token-set file states an instant."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _status(url: str, secret: str) -> int:
    """Return the HTTP status of one metrics request bearing ``secret``."""
    return http_get(url, headers={"Authorization": f"Bearer {secret}"})[0]


def _rows(db_path: Path, *kinds: str) -> list[tuple[str, float, dict[str, Any]]]:
    """Read the rows of ``kinds`` that a stopped hub left in its journal."""
    store = EventStore(db_path)
    try:
        return [
            (event.kind, event.ts, dict(event.payload)) for event in store.iter_events(kinds=kinds)
        ]
    finally:
        store.close()


def test_an_idle_hub_applies_and_rejects_a_replaced_file_within_two_intervals(
    tmp_path: Path,
) -> None:
    """No metrics request: a new file is applied and a broken one rejected by the watcher."""
    tokens = tmp_path / "metrics-tokens.json"
    _write(tokens, _entry("scraper-1", "first-secret"))
    with isolated_hub(
        tmp_path, extra_args=["--metrics", "--metrics-token-set", str(tokens)]
    ) as hub:
        url = f"http://127.0.0.1:{hub.port}/metrics"
        replaced = time.time()
        _write(tokens, _entry("scraper-2", "second-secret-longer"))
        time.sleep(_TWO_INTERVALS + 0.5)
        broken = time.time()
        _write(tokens, raw="{not json")
        time.sleep(_TWO_INTERVALS + 0.5)
        asked = time.time()
        assert _status(url, "second-secret-longer") == 200
        assert _status(url, "first-secret") == 401
        db_path = hub.db_path
    rows = _rows(db_path, METRICS_SET_APPLIED_KIND, METRICS_SET_REJECTED_KIND)
    assert [(kind, payload.get("token_ids")) for kind, _, payload in rows] == [
        (METRICS_SET_APPLIED_KIND, ["scraper-1"]),
        (METRICS_SET_APPLIED_KIND, ["scraper-2"]),
        (METRICS_SET_REJECTED_KIND, None),
    ]
    assert replaced <= rows[1][1] <= replaced + _TWO_INTERVALS
    assert broken <= rows[2][1] <= broken + _TWO_INTERVALS < asked


def test_an_entry_that_expires_while_the_hub_serves_is_one_row(tmp_path: Path) -> None:
    """``not_after`` a few seconds ahead: valid first, then 401, and one expiry row.

    The short entry is written only once the hub listens, so a slow start cannot use up
    its window; the request that follows also applies it.
    """
    tokens = tmp_path / "metrics-tokens.json"
    _write(tokens, _entry("starter", "starter-secret"))
    with isolated_hub(
        tmp_path, extra_args=["--metrics", "--metrics-token-set", str(tokens)]
    ) as hub:
        url = f"http://127.0.0.1:{hub.port}/metrics"
        end = float(int(time.time()) + 4)
        _write(tokens, _entry("short", "short-secret", not_after=_instant(end)))
        assert _status(url, "short-secret") == 200
        time.sleep(max(0.0, end + _TWO_INTERVALS + 0.5 - time.time()))
        assert _status(url, "short-secret") == 401
        assert _status(url, "short-secret") == 401
        db_path = hub.db_path
    rows = _rows(db_path, METRICS_EXPIRED_KIND)
    assert [payload for _, _, payload in rows] == [
        {"token_id": "short", "not_after": end, "observed": "watcher"}
    ]
    assert end <= rows[0][1] <= end + _TWO_INTERVALS
    assert "short-secret" not in json.dumps(rows)


def test_requests_with_the_older_of_two_valid_tokens_are_journaled_once_per_hour(
    tmp_path: Path,
) -> None:
    """A real hub journals admissions by the retiring older entry, never by the newer one.

    The test reads only what the journal itself shows, not the hub's clock: at least one
    row, each naming the older entry and its newer replacement, each at a whole hour, no
    hour twice, and no more rows than older-token requests. Which hour each admission fell
    in, and one row per entry and hour across hour changes and a clock that steps back,
    are proven with a controllable clock through public admissions in
    ``tests/test_metrics_token_watch.py``.
    """
    tokens = tmp_path / "metrics-tokens.json"
    now = time.time()
    _write(
        tokens,
        _entry("older", "older-secret", not_before=_instant(now - 7200)),
        _entry("newer", "newer-secret", not_before=_instant(now - 3600)),
    )
    with isolated_hub(
        tmp_path, extra_args=["--metrics", "--metrics-token-set", str(tokens)]
    ) as hub:
        url = f"http://127.0.0.1:{hub.port}/metrics"
        assert [_status(url, "older-secret") for _ in range(3)] == [200, 200, 200]
        assert [_status(url, "newer-secret") for _ in range(2)] == [200, 200]
        db_path = hub.db_path
    rows = [payload for _, _, payload in _rows(db_path, METRICS_RETIRING_ADMISSION_KIND)]
    assert 1 <= len(rows) <= 3
    assert {(row["token_id"], row["newest_token_id"]) for row in rows} == {("older", "newer")}
    hours = [row["hour_start"] for row in rows]
    assert all(hour % 3600 == 0 for hour in hours)
    assert len(hours) == len(set(hours))

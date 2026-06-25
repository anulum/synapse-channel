# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the NDJSON relay log and compact wire format

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from synapse_channel.relay import (
    append_jsonl,
    read_jsonl_since,
)


def test_jsonl_roundtrip_with_offset(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    append_jsonl(log, {"k": "in", "i": 1})
    first, off1 = read_jsonl_since(log, 0)
    assert len(first) == 1
    assert first[0]["i"] == 1

    append_jsonl(log, {"k": "in", "i": 2})
    second, off2 = read_jsonl_since(log, off1)
    assert len(second) == 1
    assert second[0]["i"] == 2
    assert off2 > off1


def test_append_jsonl_creates_parent_dirs(tmp_path: Path) -> None:
    log = tmp_path / "nested" / "deep" / "events.ndjson"
    append_jsonl(log, {"i": 1})
    assert log.exists()


def test_read_missing_file_returns_offset_unchanged(tmp_path: Path) -> None:
    rows, off = read_jsonl_since(tmp_path / "absent.ndjson", 7)
    assert rows == []
    assert off == 7


def test_read_empty_chunk_returns_end_offset(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    append_jsonl(log, {"i": 1})
    size = log.stat().st_size
    rows, off = read_jsonl_since(log, size)
    assert rows == []
    assert off == size


def test_read_skips_blank_and_invalid_and_non_dict_lines(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    log.write_text('{"i":1}\n\nnot-json\n[1,2,3]\n{"i":2}\n', encoding="utf-8")
    rows, _ = read_jsonl_since(log, 0)
    assert [r["i"] for r in rows] == [1, 2]


def test_read_does_not_drop_partial_tail(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    log.write_bytes(b'{"i":1}\n{"i":2')

    first, off1 = read_jsonl_since(log, 0)
    assert [row["i"] for row in first] == [1]
    assert off1 < log.stat().st_size  # partial tail was not consumed

    with log.open("ab") as handle:
        handle.write(b"}\n")

    second, off2 = read_jsonl_since(log, off1)
    assert [row["i"] for row in second] == [2]
    assert off2 == log.stat().st_size


def test_read_consumes_complete_tail_without_newline(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    log.write_bytes(b'{"i":1}')  # valid JSON, no trailing newline
    rows, off = read_jsonl_since(log, 0)
    assert [r["i"] for r in rows] == [1]
    assert off == log.stat().st_size


def test_read_consumes_non_dict_tail_without_appending(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    log.write_bytes(b'{"i":1}\n[1,2,3]')  # tail is valid JSON but not a dict
    rows, off = read_jsonl_since(log, 0)
    assert [r["i"] for r in rows] == [1]
    assert off == log.stat().st_size  # tail was consumed, just not emitted


def test_read_blank_partial_tail_stops_cleanly(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    log.write_bytes(b'{"i":1}\n   ')  # trailing whitespace-only partial line
    rows, off = read_jsonl_since(log, 0)
    assert [r["i"] for r in rows] == [1]
    assert off < log.stat().st_size


def test_read_recovers_from_truncation(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    append_jsonl(log, {"i": 1})
    append_jsonl(log, {"i": 2})
    _, off1 = read_jsonl_since(log, 0)

    log.write_text('{"i":3}\n', encoding="utf-8")  # rotation/truncation
    rows, off2 = read_jsonl_since(log, off1)
    assert [row["i"] for row in rows] == [3]
    assert off2 == log.stat().st_size


def test_read_returns_offset_on_stat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "events.ndjson"
    append_jsonl(log, {"i": 1})

    real_stat = Path.stat

    def boom(self: Path, *args: object, **kwargs: object) -> object:
        if self == log:
            raise OSError("stat failed")
        return real_stat(self)

    # Force the existence check to pass so the guarded stat() call is reached.
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "stat", boom)
    rows, off = read_jsonl_since(log, 5)
    assert rows == []
    assert off == 5


def test_append_jsonl_creates_an_owner_only_file(tmp_path: Path) -> None:
    log = tmp_path / "feed.ndjson"
    append_jsonl(log, {"i": 1})
    mode = stat.S_IMODE(os.stat(log).st_mode)
    assert mode & 0o077 == 0  # no group/other access to the plaintext relay mirror

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the NDJSON relay log and compact wire format

from __future__ import annotations

import errno
import resource
import signal
import sys
from pathlib import Path

import pytest

from synapse_channel.relay import (
    append_jsonl,
    read_jsonl_since,
    trim_jsonl_tail,
)


def test_trim_jsonl_tail(tmp_path: Path) -> None:
    log = tmp_path / "main.ndjson"
    append_jsonl(log, {"i": 1})
    append_jsonl(log, {"i": 2})
    append_jsonl(log, {"i": 3})
    dropped = trim_jsonl_tail(log, 2)
    assert dropped == 1
    rows, _ = read_jsonl_since(log, 0)
    assert [r["i"] for r in rows] == [2, 3]


def test_trim_noop_when_within_limit(tmp_path: Path) -> None:
    log = tmp_path / "main.ndjson"
    append_jsonl(log, {"i": 1})
    assert trim_jsonl_tail(log, 5) == 0


def test_trim_zero_or_missing_is_noop(tmp_path: Path) -> None:
    log = tmp_path / "main.ndjson"
    append_jsonl(log, {"i": 1})
    assert trim_jsonl_tail(log, 0) == 0
    assert trim_jsonl_tail(tmp_path / "absent.ndjson", 2) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_trim_leaves_no_temporary_files(tmp_path: Path) -> None:
    log = tmp_path / "main.ndjson"
    for i in range(5):
        append_jsonl(log, {"i": i})
    trim_jsonl_tail(log, 2)
    # The temp file was renamed into place; nothing is left in the directory.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "main.ndjson"]
    assert leftovers == []


def test_trim_replaces_the_log_with_only_the_kept_tail(tmp_path: Path) -> None:
    log = tmp_path / "main.ndjson"
    for i in range(4):
        append_jsonl(log, {"i": i})
    before_size = log.stat().st_size

    assert trim_jsonl_tail(log, 2) == 2
    assert log.stat().st_size < before_size
    rows, offset = read_jsonl_since(log, 0)
    assert [row["i"] for row in rows] == [2, 3]
    assert offset == log.stat().st_size
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "main.ndjson"]
    assert leftovers == []


@pytest.mark.skipif(
    not hasattr(resource, "RLIMIT_FSIZE"),
    reason="POSIX file size limits are required",
)
def test_trim_preserves_log_when_os_write_limit_aborts_temp_file(tmp_path: Path) -> None:
    log = tmp_path / "main.ndjson"
    for i in range(4):
        append_jsonl(log, {"i": i})
    before = log.read_text(encoding="utf-8")
    old_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
    old_handler = signal.getsignal(signal.SIGXFSZ)

    try:
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, old_limit[1]))
        with pytest.raises(OSError) as exc_info:
            trim_jsonl_tail(log, 2)
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, old_limit)
        signal.signal(signal.SIGXFSZ, old_handler)

    assert exc_info.value.errno == errno.EFBIG
    assert log.read_text(encoding="utf-8") == before
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "main.ndjson"]
    assert leftovers == []

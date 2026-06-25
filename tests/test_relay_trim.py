# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the NDJSON relay log and compact wire format

from __future__ import annotations

import os
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


def test_trim_renames_a_temp_file_onto_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "main.ndjson"
    for i in range(4):
        append_jsonl(log, {"i": i})
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: object, dst: object) -> None:
        seen.append((str(src), str(dst)))
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr("synapse_channel.relay.os.replace", spy)
    assert trim_jsonl_tail(log, 2) == 2
    # A single atomic rename moves the temp file onto the log — never an in-place write.
    assert len(seen) == 1
    assert seen[0][0] != str(log)  # source is the temp file, not the log itself
    assert seen[0][1] == str(log)


def test_trim_keeps_the_original_intact_when_the_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "main.ndjson"
    for i in range(4):
        append_jsonl(log, {"i": i})
    before = log.read_text(encoding="utf-8")

    def boom(src: object, dst: object) -> None:
        raise OSError("simulated crash before the rename completes")

    monkeypatch.setattr("synapse_channel.relay.os.replace", boom)
    with pytest.raises(OSError, match="simulated crash"):
        trim_jsonl_tail(log, 2)
    # A failed trim leaves the log byte-for-byte intact and cleans up its temp file.
    assert log.read_text(encoding="utf-8") == before
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "main.ndjson"]
    assert leftovers == []

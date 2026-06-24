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
import sys
from pathlib import Path

import pytest

from synapse_channel.relay import (
    LITE_KEYS,
    LITE_VERSION,
    append_jsonl,
    decode_lite,
    encode_lite,
    load_offset,
    normalize_core_command,
    read_jsonl_since,
    save_offset,
    trim_jsonl_tail,
)

# --- encode_lite / decode_lite codec -----------------------------------------


def test_encode_lite_uses_short_keys() -> None:
    raw = {
        "msg_id": 42,
        "type": "chat",
        "sender": "USER",
        "target": "all",
        "payload": "hello",
        "timestamp": 1700000000.123,
        "hub_id": "syn-abc",
    }
    packed = encode_lite(raw)
    assert set(packed.keys()) == {"v", "i", "ty", "s", "to", "p", "t", "h"}
    assert packed["v"] == LITE_VERSION
    assert packed["i"] == 42
    assert packed["p"] == "hello"
    assert packed["t"] == int(1700000000.123 * 1000.0)
    assert packed["h"] == "syn-abc"


def test_encode_lite_short_keys_match_the_shared_schema() -> None:
    # The codec advertises its key set; encode must emit exactly those (plus v).
    packed = encode_lite({"msg_id": 1})
    assert set(LITE_KEYS.values()) | {"v"} == set(packed.keys())


def test_encode_lite_falls_back_on_bad_timestamp_and_id() -> None:
    before_ms = int(__import__("time").time() * 1000.0)
    packed = encode_lite({"timestamp": "not-a-number", "msg_id": "nope"})
    assert packed["i"] == 0
    assert packed["t"] >= before_ms
    assert packed["ty"] == "chat"
    assert packed["s"] == "?"


def test_encode_lite_defaults_when_id_missing() -> None:
    packed = encode_lite({"timestamp": 1.0})
    assert packed["i"] == 0


def test_encode_lite_uses_now_when_timestamp_absent() -> None:
    before_ms = int(__import__("time").time() * 1000.0)
    packed = encode_lite({})
    assert packed["t"] >= before_ms


def test_decode_lite_inverts_encode_to_millisecond_precision() -> None:
    original = {
        "msg_id": 7,
        "type": "claim_granted",
        "sender": "SynapseHub",
        "target": "FAST",
        "payload": "granted H1",
        "timestamp": 1700000000.125,
        "hub_id": "syn-xyz",
    }
    restored = decode_lite(encode_lite(original))
    assert restored == {
        "sender": "SynapseHub",
        "target": "FAST",
        "type": "claim_granted",
        "payload": "granted H1",
        "timestamp": 1700000000.125,
        "msg_id": 7,
        "hub_id": "syn-xyz",
    }


def test_decode_lite_uses_defaults_for_missing_and_malformed_keys() -> None:
    restored = decode_lite({"t": "bad", "i": "bad"})
    assert restored == {
        "sender": "?",
        "target": "all",
        "type": "chat",
        "payload": "",
        "timestamp": 0.0,
        "msg_id": 0,
        "hub_id": "",
    }


# --- append / read roundtrip -------------------------------------------------


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


# --- offset persistence ------------------------------------------------------


def test_offset_persistence_roundtrip(tmp_path: Path) -> None:
    marker = tmp_path / "cur" / "offset"
    assert load_offset(marker) == 0
    save_offset(marker, 123)
    assert load_offset(marker) == 123


def test_save_offset_clamps_negative(tmp_path: Path) -> None:
    marker = tmp_path / "offset"
    save_offset(marker, -5)
    assert load_offset(marker) == 0


def test_load_offset_corrupt_returns_zero(tmp_path: Path) -> None:
    marker = tmp_path / "offset"
    marker.write_text("garbage", encoding="utf-8")
    assert load_offset(marker) == 0


# --- trim --------------------------------------------------------------------


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
def test_append_jsonl_creates_an_owner_only_file(tmp_path: Path) -> None:
    log = tmp_path / "feed.ndjson"
    append_jsonl(log, {"i": 1})
    mode = stat.S_IMODE(os.stat(log).st_mode)
    assert mode & 0o077 == 0  # no group/other access to the plaintext relay mirror


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


# --- normalize_core_command --------------------------------------------------


def test_normalize_requires_kind() -> None:
    with pytest.raises(ValueError, match="Missing command kind"):
        normalize_core_command({})


def test_normalize_rejects_unsupported_kind() -> None:
    with pytest.raises(ValueError, match="Unsupported command kind"):
        normalize_core_command({"k": "explode"})


def test_normalize_chat_and_claim_short_aliases() -> None:
    assert normalize_core_command({"kind": "chat", "payload": " hi ", "target": "USER"}) == {
        "k": "chat",
        "p": "hi",
        "to": "USER",
    }
    assert normalize_core_command({"k": "claim", "task_id": "H1", "note": "x"}) == {
        "k": "claim",
        "id": "H1",
        "n": "x",
    }


def test_normalize_release_and_who_and_state() -> None:
    assert normalize_core_command({"k": "release", "id": "H2"}) == {"k": "release", "id": "H2"}
    assert normalize_core_command({"k": "who"}) == {"k": "who"}
    assert normalize_core_command({"k": "state"}) == {"k": "state"}


def test_normalize_history_variants() -> None:
    assert normalize_core_command({"k": "history", "limit": "999"}) == {"k": "history", "n": 999}
    assert normalize_core_command({"k": "history", "limit": "all"}) == {"k": "history", "n": "all"}
    assert normalize_core_command({"k": "history"}) == {"k": "history", "n": 20}
    assert normalize_core_command({"k": "history", "n": "bad"}) == {"k": "history", "n": 20}
    assert normalize_core_command({"k": "history", "n": -3}) == {"k": "history", "n": 1}


def test_normalize_task_update_full_and_minimal() -> None:
    full = normalize_core_command(
        {"k": "task_update", "task_id": "T", "status": "done", "note": "n", "data_ref": "r"}
    )
    assert full == {"k": "task_update", "id": "T", "status": "done", "note": "n", "data_ref": "r"}

    minimal = normalize_core_command({"k": "task_update", "id": "T", "status": ""})
    assert minimal == {"k": "task_update", "id": "T"}


def test_normalize_resource_with_and_without_meta() -> None:
    with_meta = normalize_core_command(
        {"k": "resource", "kind": "llm", "name": "m", "capacity": 2, "meta": {"vram": "8G"}}
    )
    assert with_meta == {
        "k": "resource",
        "kind": "llm",
        "name": "m",
        "capacity": 2,
        "meta": {"vram": "8G"},
    }

    without_meta = normalize_core_command(
        {"k": "resource_offer", "resource_kind": "fs", "resource_name": "disk"}
    )
    assert without_meta == {
        "k": "resource_offer",
        "kind": "fs",
        "name": "disk",
        "capacity": 1,
    }

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import os
from pathlib import Path

import pytest

from synapse_channel.opencode_adapter_files import (
    OpenCodeAdapterFileError,
    read_text_snapshot,
    remove_snapshot,
    write_text_snapshot,
)


def _write_private(path: Path, value: str | bytes) -> None:
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value)
    os.chmod(path, 0o600)


def test_new_file_round_trip_is_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.json"
    snapshot = read_text_snapshot(path)
    write_text_snapshot(path, "{}\n", snapshot)
    assert path.read_text() == "{}\n"
    assert path.stat().st_mode & 0o777 == 0o600
    captured = read_text_snapshot(path)
    remove_snapshot(path, captured)
    assert not path.exists()


def test_concurrent_change_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_private(path, "one")
    snapshot = read_text_snapshot(path)
    _write_private(path, "two")
    with pytest.raises(OpenCodeAdapterFileError, match="changed concurrently"):
        write_text_snapshot(path, "three", snapshot)


def test_same_size_rewrite_is_refused_even_when_stat_metadata_collides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a filesystem where a same-size rewrite lands in one mtime/ctime
    # tick: freeze the stat fingerprint so it can no longer tell the two writes
    # apart. Only the content digest can catch "one" -> "two" (same length),
    # which is exactly the TOCTOU the reviewer reproduced on their hardware.
    from synapse_channel import opencode_adapter_files as module

    path = tmp_path / "config.json"
    _write_private(path, "one")
    frozen = module._fingerprint(os.stat(path))
    monkeypatch.setattr(module, "_fingerprint", lambda _info: frozen)
    snapshot = read_text_snapshot(path)
    _write_private(path, "two")  # same size; metadata frozen-identical
    with pytest.raises(OpenCodeAdapterFileError, match="changed concurrently"):
        write_text_snapshot(path, "three", snapshot)


def test_leaf_symlink_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _write_private(target, "x")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OpenCodeAdapterFileError, match="regular file"):
        read_text_snapshot(link)


def test_oversized_and_non_utf8_files_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad"
    _write_private(path, b"x" * 20)
    with pytest.raises(OpenCodeAdapterFileError, match="exceeds"):
        read_text_snapshot(path, limit=10)
    _write_private(path, b"\xff")
    with pytest.raises(OpenCodeAdapterFileError, match="not UTF-8"):
        read_text_snapshot(path)


def test_safe_existing_mode_is_preserved_and_writable_mode_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config"
    _write_private(path, "old")
    os.chmod(path, 0o640)
    snapshot = read_text_snapshot(path)
    write_text_snapshot(path, "new", snapshot)
    assert path.stat().st_mode & 0o777 == 0o640

    unsafe = tmp_path / "unsafe"
    _write_private(unsafe, "owned marker")
    os.chmod(unsafe, 0o666)
    with pytest.raises(OpenCodeAdapterFileError, match="writable by group or others"):
        read_text_snapshot(unsafe)


def test_appeared_disappeared_and_oversized_write_are_refused(tmp_path: Path) -> None:
    appeared = tmp_path / "appeared"
    missing = read_text_snapshot(appeared)
    _write_private(appeared, "now here")
    with pytest.raises(OpenCodeAdapterFileError, match="appeared concurrently"):
        write_text_snapshot(appeared, "new", missing)

    disappeared = tmp_path / "disappeared"
    _write_private(disappeared, "old")
    captured = read_text_snapshot(disappeared)
    disappeared.unlink()
    with pytest.raises(OpenCodeAdapterFileError, match="disappeared"):
        remove_snapshot(disappeared, captured)

    with pytest.raises(OpenCodeAdapterFileError, match="too large"):
        large = tmp_path / "large"
        write_text_snapshot(large, "x" * 1_048_577, read_text_snapshot(large))


def test_parent_symlink_and_wrong_owner_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    path = linked / "new"
    with pytest.raises(OpenCodeAdapterFileError, match="parent is unsafe"):
        write_text_snapshot(path, "x", read_text_snapshot(path))

    writable_parent = tmp_path / "writable"
    writable_parent.mkdir(mode=0o700)
    os.chmod(writable_parent, 0o777)
    with pytest.raises(OpenCodeAdapterFileError, match="writable by group or others"):
        read_text_snapshot(writable_parent / "plugin.js")

    owned = tmp_path / "owned"
    _write_private(owned, "x")
    monkeypatch.setattr(os, "getuid", lambda: owned.stat().st_uid + 1)
    with pytest.raises(OpenCodeAdapterFileError, match="not owned"):
        read_text_snapshot(owned)


def test_remove_missing_snapshot_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "missing"
    remove_snapshot(path, read_text_snapshot(path))
    assert not path.exists()

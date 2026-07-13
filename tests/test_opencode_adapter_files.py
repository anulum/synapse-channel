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
    path.write_text("one")
    snapshot = read_text_snapshot(path)
    path.write_text("two")
    with pytest.raises(OpenCodeAdapterFileError, match="changed concurrently"):
        write_text_snapshot(path, "three", snapshot)


def test_leaf_symlink_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OpenCodeAdapterFileError, match="regular file"):
        read_text_snapshot(link)


def test_oversized_and_non_utf8_files_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad"
    path.write_bytes(b"x" * 20)
    with pytest.raises(OpenCodeAdapterFileError, match="exceeds"):
        read_text_snapshot(path, limit=10)
    path.write_bytes(b"\xff")
    with pytest.raises(OpenCodeAdapterFileError, match="not UTF-8"):
        read_text_snapshot(path)


def test_existing_mode_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "config"
    path.write_text("old")
    os.chmod(path, 0o640)
    snapshot = read_text_snapshot(path)
    write_text_snapshot(path, "new", snapshot)
    assert path.stat().st_mode & 0o777 == 0o640


def test_appeared_disappeared_and_oversized_write_are_refused(tmp_path: Path) -> None:
    appeared = tmp_path / "appeared"
    missing = read_text_snapshot(appeared)
    appeared.write_text("now here")
    with pytest.raises(OpenCodeAdapterFileError, match="appeared concurrently"):
        write_text_snapshot(appeared, "new", missing)

    disappeared = tmp_path / "disappeared"
    disappeared.write_text("old")
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
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    path = linked / "new"
    with pytest.raises(OpenCodeAdapterFileError, match="parent is unsafe"):
        write_text_snapshot(path, "x", read_text_snapshot(path))

    owned = tmp_path / "owned"
    owned.write_text("x")
    monkeypatch.setattr(os, "getuid", lambda: owned.stat().st_uid + 1)
    with pytest.raises(OpenCodeAdapterFileError, match="not owned"):
        read_text_snapshot(owned)


def test_remove_missing_snapshot_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "missing"
    remove_snapshot(path, read_text_snapshot(path))
    assert not path.exists()

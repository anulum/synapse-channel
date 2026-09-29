# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the on-disk lease epoch store stays contained, private and fail-soft
"""Tests for :mod:`synapse_channel.client.lease_epoch_store` on the real filesystem."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from _platform_caps import requires_posix_mode_bits
from synapse_channel.client.lease_epoch_store import LeaseEpochStore, default_lease_epoch_root


def test_an_epoch_round_trips_and_is_forgotten(tmp_path: Path) -> None:
    store = LeaseEpochStore("P/alice", root=tmp_path)
    assert store.load("hub-1", "T1") is None
    store.save("hub-1", "T1", 7)
    store.save("hub-1", "T1", 9)  # a newer grant replaces the older one
    assert store.load("hub-1", "T1") == 9
    assert store.load("hub-2", "T1") is None  # epochs are per hub
    assert LeaseEpochStore("P/bob", root=tmp_path).load("hub-1", "T1") is None
    store.forget("hub-1", "T1")
    store.forget("hub-1", "T1")  # forgetting twice is harmless
    assert store.load("hub-1", "T1") is None


@requires_posix_mode_bits
def test_files_are_owner_only_and_names_stay_flat(tmp_path: Path) -> None:
    store = LeaseEpochStore("P/alice", root=tmp_path)
    store.save("hub/1", "a/b", 3)
    [written] = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert written.relative_to(tmp_path).parts == ("P%2Falice", "hub%2F1", "a%2Fb")
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert stat.S_IMODE(written.parent.stat().st_mode) == 0o700
    assert written.read_text(encoding="ascii") == "3"


@pytest.mark.parametrize("name", [".", "..", "../x", "a.b"])
def test_dot_components_cannot_climb_out(tmp_path: Path, name: str) -> None:
    root = tmp_path / "root"
    store = LeaseEpochStore(name, root=root)
    store.save(name, name, 1)
    assert store.load(name, name) == 1
    [written] = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert written.parent.parent.parent == root  # root / identity / hub / task
    assert all(part not in {".", ".."} for part in written.relative_to(root).parts)


def test_empty_names_are_never_stored(tmp_path: Path) -> None:
    for identity, hub_id, task_id in (("", "h", "t"), ("i", "", "t"), ("i", "h", "")):
        store = LeaseEpochStore(identity, root=tmp_path)
        store.save(hub_id, task_id, 1)
        store.forget(hub_id, task_id)
        assert store.load(hub_id, task_id) is None
    assert list(tmp_path.iterdir()) == []


def test_a_negative_epoch_is_not_stored(tmp_path: Path) -> None:
    store = LeaseEpochStore("i", root=tmp_path)
    store.save("h", "t", -1)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [b"", b"abc", b"-3", b"01", b"1.5", b"9" * 33, b"\xff\xfe"],
)
def test_a_malformed_file_reads_as_no_epoch(tmp_path: Path, content: bytes) -> None:
    store = LeaseEpochStore("i", root=tmp_path)
    store.save("h", "t", 1)
    [written] = [path for path in tmp_path.rglob("*") if path.is_file()]
    written.write_bytes(content)
    assert store.load("h", "t") is None


def test_surrounding_whitespace_is_tolerated(tmp_path: Path) -> None:
    store = LeaseEpochStore("i", root=tmp_path)
    store.save("h", "t", 1)
    [written] = [path for path in tmp_path.rglob("*") if path.is_file()]
    written.write_text("42\n", encoding="ascii")
    assert store.load("h", "t") == 42


@requires_posix_mode_bits
def test_an_unwritable_store_is_ignored(tmp_path: Path) -> None:
    store = LeaseEpochStore("i", root=tmp_path / "root")
    store.save("h", "t", 1)
    hub_dir = tmp_path / "root" / "i" / "h"
    hub_dir.chmod(0o500)
    try:
        store.save("h", "u", 2)  # the temporary file cannot be created
        assert store.load("h", "u") is None
        store.forget("h", "t")  # the entry cannot be removed
        assert store.load("h", "t") == 1
    finally:
        hub_dir.chmod(0o700)
    (tmp_path / "blocked").write_text("", encoding="ascii")
    blocked = LeaseEpochStore("i", root=tmp_path / "blocked")  # the root is a file
    blocked.save("h", "t", 3)
    assert blocked.load("h", "t") is None


def test_a_directory_in_place_of_the_file_is_ignored(tmp_path: Path) -> None:
    store = LeaseEpochStore("i", root=tmp_path)
    store.save("h", "t", 1)
    [written] = [path for path in tmp_path.rglob("*") if path.is_file()]
    written.unlink()
    written.mkdir()
    store.save("h", "t", 2)  # the atomic replace fails onto a directory
    assert store.load("h", "t") is None
    assert [path.name for path in written.parent.iterdir()] == ["t"]  # no temp left behind
    store.forget("h", "t")  # unlinking a directory fails and is ignored
    assert written.is_dir()


def test_the_default_root_follows_the_data_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert default_lease_epoch_root() == tmp_path / "data" / "synapse" / "lease-epoch"
    monkeypatch.setenv("XDG_DATA_HOME", "  ")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert default_lease_epoch_root() == (
        tmp_path / "home" / ".local" / "share" / "synapse" / "lease-epoch"
    )
    assert default_lease_epoch_root(base=tmp_path) == tmp_path / "synapse" / "lease-epoch"
    assert LeaseEpochStore("i").directory == tmp_path / "home" / ".local" / "share" / (
        "synapse/lease-epoch/i"
    )

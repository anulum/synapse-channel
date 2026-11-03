# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual descriptor-relative runtime inventory boundaries
"""Exercise exact inventory, retained-root replacement and finite metadata enrollment."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from tools.protected_runtime_probe.inventory import verify_directory_inventory

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX directory descriptors")


@pytest.fixture
def runtime_directory(tmp_path: Path) -> Iterator[tuple[Path, int, frozenset[str]]]:
    """Retain an actual directory until the caller's complete observation finishes."""
    root = tmp_path / "image"
    (root / "package").mkdir(parents=True)
    (root / "package/module.py").write_bytes(b"approved fixture bytes")
    (root / "resource.bin").write_bytes(b"resource")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield root, descriptor, frozenset({"package/module.py", "resource.bin"})
    finally:
        os.close(descriptor)


def test_exact_actual_tree_preserves_borrowed_descriptor_and_bytes(
    runtime_directory: tuple[Path, int, frozenset[str]],
) -> None:
    """Matching native directories return without changing files or consuming custody."""
    root, descriptor, files = runtime_directory
    before = os.fstat(descriptor)
    verify_directory_inventory(descriptor, files)
    after = os.fstat(descriptor)
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        before.st_dev,
        before.st_ino,
        before.st_mode,
    )
    assert (root / "package/module.py").read_bytes() == b"approved fixture bytes"


@pytest.mark.parametrize(
    "change",
    [
        "extra-file",
        "empty-tree",
        "missing-file",
        "file-link",
        "parent-link",
        "hardlink",
        "fifo",
        "directory-as-file",
        "file-as-directory",
    ],
)
def test_changed_actual_tree_refuses_without_traversing_unenrolled_content(
    tmp_path: Path,
    runtime_directory: tuple[Path, int, frozenset[str]],
    change: str,
) -> None:
    """Real altered names, links, object kinds and deep junk fail the enrolled scope."""
    root, descriptor, files = runtime_directory
    leaf = root / "resource.bin"
    if change == "extra-file":
        (root / "unlisted").write_bytes(b"not enrolled")
    elif change == "empty-tree":
        deep = root / "unlisted"
        for _ in range(65):
            deep /= "d"
        deep.mkdir(parents=True)
    elif change == "missing-file":
        leaf.unlink()
    elif change == "parent-link":
        (root / "package").rename(tmp_path / "external-package")
        (root / "package").symlink_to(tmp_path / "external-package", target_is_directory=True)
    elif change == "directory-as-file":
        (root / "package/module.py").unlink()
        (root / "package").rmdir()
        (root / "package").write_bytes(b"not a directory")
    elif change == "hardlink":
        os.link(leaf, tmp_path / "outside-runtime")
    else:
        leaf.unlink()
        if change == "file-link":
            (tmp_path / "outside-runtime").write_bytes(b"resource")
            leaf.symlink_to(tmp_path / "outside-runtime")
        elif change == "fifo":
            os.mkfifo(leaf)
        else:
            leaf.mkdir()
    with pytest.raises(ValueError):
        verify_directory_inventory(descriptor, files)
    assert os.fstat(descriptor).st_ino == root.stat().st_ino


def test_retained_root_survives_actual_rename_and_path_impostor(
    tmp_path: Path, runtime_directory: tuple[Path, int, frozenset[str]]
) -> None:
    """Reusing the old pathname cannot redirect the descriptor's inventory scan."""
    root, descriptor, files = runtime_directory
    original = os.fstat(descriptor)
    retained = tmp_path / "retained"
    root.rename(retained)
    root.mkdir()
    os.mkfifo(root / "unlisted-impostor")
    verify_directory_inventory(descriptor, files)
    assert os.fstat(descriptor).st_ino == original.st_ino == retained.stat().st_ino
    assert root.stat().st_ino != original.st_ino


@pytest.mark.parametrize("descriptor", [-1, True, "descriptor"])
def test_invalid_borrowed_descriptor_refuses_before_any_scan(descriptor: object) -> None:
    """A boolean or caller string cannot identify an enrolled kernel directory."""
    with pytest.raises(ValueError):
        verify_directory_inventory(cast(int, descriptor), frozenset({"file"}))


def test_actual_file_descriptor_is_not_a_directory(tmp_path: Path) -> None:
    """An open regular file never supplies directory custody."""
    path = tmp_path / "file"
    path.write_bytes(b"ordinary file")
    descriptor = os.open(path, os.O_RDONLY)
    try:
        with pytest.raises(ValueError):
            verify_directory_inventory(descriptor, frozenset({"file"}))
        assert os.read(descriptor, 1) == b"o"
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    "files",
    [
        set(),
        frozenset(),
        frozenset({""}),
        frozenset({"/absolute"}),
        frozenset({"parent/../file"}),
        frozenset({"parent/./file"}),
        frozenset({"parent//file"}),
        frozenset({"back\\slash"}),
        frozenset({"line\nbreak"}),
        frozenset({123}),
        frozenset({"a" * 4097}),
        frozenset({"/".join(["nested"] * 65)}),
        frozenset({"file", "file/child"}),
        frozenset(str(index) for index in range(10_001)),
        frozenset(f"{index}/child/file" for index in range(10_000)),
        frozenset(f"{index:04d}" + "d" * 1020 + "/file" for index in range(5000)),
    ],
    ids=[
        "mutable-scope",
        "empty-scope",
        "empty-name",
        "absolute",
        "parent",
        "dot",
        "empty-component",
        "backslash",
        "control-character",
        "non-string",
        "name-byte-budget",
        "depth-budget",
        "file-directory-conflict",
        "file-count-budget",
        "entry-count-budget",
        "metadata-byte-budget",
    ],
)
def test_invalid_or_excessive_inventory_refuses_without_mutating_actual_tree(
    runtime_directory: tuple[Path, int, frozenset[str]], files: object
) -> None:
    """Untrusted enrollment cannot create an unbounded metadata walk or unsafe ancestry."""
    root, descriptor, _enrolled = runtime_directory
    with pytest.raises(ValueError):
        verify_directory_inventory(descriptor, cast(frozenset[str], files))
    assert (root / "resource.bin").read_bytes() == b"resource"

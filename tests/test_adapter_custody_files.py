# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — unsafe adapter file admission regressions
"""Exercise actual target and journal filesystem boundaries through the CLI."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.adapter_custody import manage_adapter
from synapse_channel.adapter_custody_files import AdapterCustodyError
from synapse_channel.adapters import render_block, tool_for
from synapse_channel.cli import main


@pytest.mark.parametrize(
    "kind", ["leaf-link", "parent-link", "directory", "hardlink", "invalid-utf8", "oversized"]
)
def test_cli_refuses_unsafe_targets_without_changing_owner_data(tmp_path: Path, kind: str) -> None:
    """Neither replaceable paths nor files outside the bounded text contract are adopted."""
    outside = tmp_path / "outside"
    outside.mkdir()
    owner = outside / "owner.md"
    owner.write_bytes(b"owner rules\r\n")
    parent = tmp_path / ".codex"
    if kind == "parent-link":
        parent.symlink_to(outside, target_is_directory=True)
    else:
        parent.mkdir()
    target = parent / "synapse.md"
    if kind == "leaf-link":
        target.symlink_to(owner)
    elif kind == "directory":
        target.mkdir()
    elif kind == "hardlink":
        os.link(owner, target)
    elif kind == "invalid-utf8":
        target.write_bytes(b"\xff\xfe")
    elif kind == "oversized":
        target.write_bytes(b"a" * 1_048_577)
    for action in ["list", "install", "uninstall", "recover"]:
        extra = ["--adopt-existing"] if action == "install" else []
        assert main(["adapters", action, "codex", *extra, "--home", str(tmp_path)]) == 2
    assert owner.read_bytes() == b"owner rules\r\n"


@pytest.mark.parametrize("relative", ["../owner.md", "/outside/owner.md", "."])
def test_runtime_api_refuses_targets_outside_catalogue_root(tmp_path: Path, relative: str) -> None:
    """Custom catalogue entries cannot authorize traversal or a directory target."""
    tool = replace(tool_for("codex"), target=relative)
    with pytest.raises(AdapterCustodyError):
        manage_adapter(tool, root=tmp_path, action="install")


@pytest.mark.parametrize("entry", ["lock", "custody.sqlite3", "directory"])
def test_linked_custody_entries_do_not_mutate_external_files(tmp_path: Path, entry: str) -> None:
    """Private state cannot be redirected through a pre-existing symbolic link."""
    tool = tool_for("codex")
    target = tmp_path / tool.target
    target.parent.mkdir(parents=True)
    owner = tmp_path / "owner"
    owner.write_bytes(b"owner data")
    directory = target.parent / ".synapse.md.synapse-custody"
    if entry == "directory":
        directory.symlink_to(tmp_path, target_is_directory=True)
    else:
        directory.mkdir(mode=0o700)
        (directory / entry).symlink_to(owner)
    block = render_block(tool, identity="owner/agent", hub_uri="ws://localhost:8876")
    with pytest.raises((OSError, ValueError)):
        manage_adapter(tool, root=tmp_path, action="install", block=block)
    assert owner.read_bytes() == b"owner data"
    assert not target.exists()


@pytest.mark.parametrize("entry", ["lock", "custody.sqlite3"])
def test_exposed_custody_state_is_not_used_for_mutation(tmp_path: Path, entry: str) -> None:
    """Readable recovery state fails its declared owner-only contract."""
    tool = tool_for("codex")
    block = render_block(tool, identity="owner", hub_uri="ws://localhost:8876")
    manage_adapter(tool, root=tmp_path, action="install", block=block)
    target = tmp_path / tool.target
    state = target.parent / ".synapse.md.synapse-custody" / entry
    state.chmod(0o644)
    with pytest.raises(AdapterCustodyError, match="owner-only"):
        manage_adapter(tool, root=tmp_path, action="uninstall")
    assert target.read_bytes() == block.encode()


def test_file_effect_refuses_changed_snapshot_and_retains_prepared_intent(tmp_path: Path) -> None:
    """A real external rewrite between capture and effect cannot be overwritten."""
    from synapse_channel.adapter_custody_files import replace_target
    from synapse_channel.adapter_custody_store import CustodyStore, Ownership, PendingUpdate
    from synapse_channel.opencode_adapter_files import read_text_snapshot

    tool = tool_for("codex")
    target = tmp_path / tool.target
    target.parent.mkdir()
    target.write_bytes(b"original")
    directory = target.parent / ".synapse.md.synapse-custody"
    directory.mkdir(mode=0o700)
    journal = directory / "custody.sqlite3"
    block = render_block(tool, identity="owner", hub_uri="ws://localhost:8876")
    with CustodyStore(journal, target) as store:
        pending = PendingUpdate("original", block, Ownership("codex", "file", "original", block))
        store.save(None, pending)
        snapshot = read_text_snapshot(target)
        target.write_bytes(b"external writer")
        with pytest.raises(AdapterCustodyError, match="changed during"):
            replace_target(target, block, snapshot, directory=directory)
        assert store.load() == (None, pending)
    assert target.read_bytes() == b"external writer"
    assert (directory / "replacement").read_bytes() == block.encode()


def test_linked_ancestor_above_existing_owned_parent_is_refused(tmp_path: Path) -> None:
    """Checking only the nearest existing owned directory must not hide an ancestor link."""
    outside = tmp_path / "outside"
    (outside / "nested").mkdir(parents=True)
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    tool = replace(tool_for("codex"), target="linked/nested/synapse.md")
    with pytest.raises(AdapterCustodyError, match="parent is a symbolic link"):
        manage_adapter(
            tool,
            root=tmp_path,
            action="install",
            block=render_block(tool, identity="owner", hub_uri="ws://localhost:8876"),
        )
    assert not (outside / "nested/synapse.md").exists()


def test_file_effect_deletion_of_captured_absence_creates_no_target(tmp_path: Path) -> None:
    """The recoverable filesystem effect preserves absence when asked to remove absence."""
    from synapse_channel.adapter_custody_files import replace_target
    from synapse_channel.opencode_adapter_files import read_text_snapshot

    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    target = tmp_path / "absent"
    replace_target(target, None, read_text_snapshot(target), directory=directory)
    assert not target.exists()
    assert list(directory.iterdir()) == []


def test_replaceable_parent_is_refused_without_touching_owner_content(tmp_path: Path) -> None:
    """An owned but group-writable parent is not a safe installation boundary."""
    parent = tmp_path / ".codex"
    parent.mkdir()
    target = parent / "synapse.md"
    target.write_bytes(b"owner")
    parent.chmod(0o777)
    try:
        assert (
            main(["adapters", "install", "codex", "--adopt-existing", "--home", str(tmp_path)]) == 2
        )
        assert target.read_bytes() == b"owner"
    finally:
        parent.chmod(0o700)

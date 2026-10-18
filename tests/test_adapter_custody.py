# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — public adapter custody lifecycle regressions
"""Exercise exact-byte ownership through the registered CLI and runtime API."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.adapter_custody import manage_adapter
from synapse_channel.adapters import CATALOGUE, FILE_MODE, render_block, resolve_target, tool_for
from synapse_channel.cli import main


def invoke(root: Path, action: str, *options: str) -> int:
    """Run the registered generic installer against an isolated home and project."""
    return main(["adapters", action, *options, "--home", str(root), "--project", str(root)])


@pytest.mark.parametrize("key", [tool.key for tool in CATALOGUE if tool.mode == FILE_MODE])
def test_dedicated_files_require_adoption_and_restore_exact_original(
    tmp_path: Path, key: str
) -> None:
    """All dedicated formats, including complete skill frontmatter, retain originals."""
    tool = tool_for(key)
    target = resolve_target(tool, home=tmp_path, project=tmp_path, environ={})
    target.parent.mkdir(parents=True)
    original = b"Owner instructions.  \r\n\r\n"
    target.write_bytes(original)
    assert invoke(tmp_path, "install", key) == 2
    assert target.read_bytes() == original
    assert not list(target.parent.glob(".*.synapse-custody"))
    assert invoke(tmp_path, "install", key, "--adopt-existing") == 0
    installed = target.read_bytes()
    if tool.comment in {"skill", "grok-skill"}:
        assert installed.startswith(b"---\nname: synapse\n")
    assert invoke(tmp_path, "install", key) == 0
    assert target.read_bytes() == installed
    assert invoke(tmp_path, "uninstall", key) == 0
    assert target.read_bytes() == original


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_changed_dedicated_file_refuses_mutation(tmp_path: Path, action: str) -> None:
    """An owner suffix is preserved, even though the old adapter markers remain."""
    assert invoke(tmp_path, "install", "codex") == 0
    target = tmp_path / ".codex/synapse.md"
    changed = target.read_bytes() + b"\nOwner added this.\r\n"
    target.write_bytes(changed)
    options = ["--adopt-existing"] if action == "install" else []
    assert invoke(tmp_path, action, "codex", *options) == 2
    assert target.read_bytes() == changed


@pytest.mark.parametrize("key", ["aider", "windsurf"])
@pytest.mark.parametrize("original", [b"", b"Owner  \r\n\r\n", b"Owner without newline  "])
def test_shared_files_preserve_outside_bytes_across_updates(
    tmp_path: Path, key: str, original: bytes
) -> None:
    """Updating or removing the exact owned segment preserves other writers' text."""
    tool = tool_for(key)
    target = resolve_target(tool, home=tmp_path, project=tmp_path, environ={})
    target.write_bytes(original)
    assert invoke(tmp_path, "install", key) == 0
    prefix, suffix = b"New prefix\r\n", b"\r\nNew suffix  \r\n"
    target.write_bytes(prefix + target.read_bytes() + suffix)
    assert invoke(tmp_path, "install", key, "--identity", "changed/agent") == 0
    assert b"changed/agent" in target.read_bytes()
    assert invoke(tmp_path, "uninstall", key) == 0
    assert target.read_bytes() == prefix + original + suffix


@pytest.mark.parametrize("original", [None, b""])
def test_shared_uninstall_distinguishes_absent_and_empty_original(
    tmp_path: Path, original: bytes | None
) -> None:
    """Only an originally absent file with no outside additions is removed."""
    target = tmp_path / "CONVENTIONS.md"
    if original is not None:
        target.write_bytes(original)
    assert invoke(tmp_path, "install", "aider") == 0
    assert invoke(tmp_path, "uninstall", "aider") == 0
    assert target.exists() is (original is not None)
    if original is not None:
        assert target.read_bytes() == original


@pytest.mark.parametrize("key", ["codex", "aider"])
def test_dry_run_is_read_only_for_new_and_recorded_targets(tmp_path: Path, key: str) -> None:
    """Preview reads existing custody without creating locks or changing journal bytes."""
    assert invoke(tmp_path, "install", key, "--dry-run") == 0
    assert list(tmp_path.iterdir()) == []
    assert invoke(tmp_path, "install", key) == 0
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert invoke(tmp_path, "install", key, "--dry-run", "--identity", "updated/agent") == 0
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize("key", ["codex", "aider"])
def test_unrecorded_markers_never_authorize_deletion(tmp_path: Path, key: str) -> None:
    """Legacy marker-only files require deliberate dedicated adoption or manual merging."""
    tool = tool_for(key)
    target = resolve_target(tool, home=tmp_path, project=tmp_path, environ={})
    target.parent.mkdir(parents=True, exist_ok=True)
    original = render_block(tool, identity="old/agent", hub_uri="ws://localhost:8876").encode()
    target.write_bytes(original)
    assert invoke(tmp_path, "uninstall", key) == 2
    assert target.read_bytes() == original


def test_shared_edited_or_duplicate_block_is_preserved(tmp_path: Path) -> None:
    """Neither a body change nor duplicate markers are treated as owned bytes."""
    assert invoke(tmp_path, "install", "aider") == 0
    target = tmp_path / "CONVENTIONS.md"
    installed = target.read_bytes()
    changed = installed.replace(b"claim before edit", b"owner changed content")
    # The rendered heading uses a different case; change a guaranteed body byte.
    changed = changed.replace(b"Claim before edit", b"Owner changed content")
    target.write_bytes(changed)
    assert invoke(tmp_path, "uninstall", "aider") == 2
    assert target.read_bytes() == changed
    target.write_bytes(installed + installed)
    assert invoke(tmp_path, "install", "aider") == 2
    assert target.read_bytes() == installed + installed


def test_public_api_updates_owned_dedicated_and_recovers_unmanaged_absence(tmp_path: Path) -> None:
    """The runtime API and CLI share the same dedicated-file ownership admission."""
    tool = tool_for("codex")
    assert manage_adapter(tool, root=tmp_path, action="recover") == "not-installed"
    first = render_block(tool, identity="first", hub_uri="ws://localhost:8876")
    second = render_block(tool, identity="second", hub_uri="ws://localhost:8876")
    assert manage_adapter(tool, root=tmp_path, action="install", block=first) == "installed"
    assert manage_adapter(tool, root=tmp_path, action="install", block=second) == "updated"
    assert invoke(tmp_path, "recover", "codex") == 0
    assert (tmp_path / tool.target).read_bytes() == second.encode()
    assert invoke(tmp_path, "recover", "unknown-tool") == 2


def test_real_cli_processes_round_trip_adopted_owner_file(tmp_path: Path) -> None:
    """Separate installed CLI invocations retain custody across process lifetimes."""
    target = tmp_path / ".codex/synapse.md"
    target.parent.mkdir()
    original = b"Owner\r\n  \r\n"
    target.write_bytes(original)
    for action, options in [("install", ["--adopt-existing"]), ("uninstall", [])]:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "synapse_channel.cli",
                "adapters",
                action,
                "codex",
                *options,
                "--home",
                str(tmp_path),
                "--project",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    assert target.read_bytes() == original


def test_runtime_rejects_invalid_marker_order_and_unsupported_modes(tmp_path: Path) -> None:
    """Invalid public API input cannot create ownership state."""
    from dataclasses import replace

    from synapse_channel.adapter_custody_files import AdapterCustodyError
    from synapse_channel.adapters import MARKER_BEGIN, MARKER_END

    tool = tool_for("codex")
    with pytest.raises(AdapterCustodyError, match="marker order"):
        manage_adapter(tool, root=tmp_path, action="install", block=f"{MARKER_END}\n{MARKER_BEGIN}")
    with pytest.raises(AdapterCustodyError, match="Unsupported"):
        manage_adapter(replace(tool, mode="unrecognized"), root=tmp_path, action="install")
    assert not list(tmp_path.iterdir())


def test_removing_target_externally_does_not_silently_discard_ownership(tmp_path: Path) -> None:
    """A missing previously owned dedicated file requires inspection."""
    assert invoke(tmp_path, "install", "codex") == 0
    (tmp_path / ".codex/synapse.md").unlink()
    assert invoke(tmp_path, "uninstall", "codex") == 2


def test_adopting_identical_bytes_and_previewing_recovery(tmp_path: Path) -> None:
    """Ownership can be acquired without rewriting an identical existing file."""
    tool = tool_for("codex")
    target = tmp_path / tool.target
    target.parent.mkdir()
    block = render_block(tool, identity="owner", hub_uri="ws://localhost:8876")
    target.write_bytes(block.encode())
    before = target.stat().st_mtime_ns
    manage_adapter(tool, root=tmp_path, action="install", block=block, adopt_existing=True)
    assert target.stat().st_mtime_ns == before
    assert manage_adapter(tool, root=tmp_path, action="recover", dry_run=True) == "would write"
    manage_adapter(tool, root=tmp_path, action="uninstall")
    assert target.read_bytes() == block.encode()


def test_runtime_preserves_leading_newlines_in_owned_append_block(tmp_path: Path) -> None:
    """Reinstall distinguishes the original separator from the rendered block's own prefix."""
    tool = tool_for("aider")
    block = "\n\n" + render_block(tool, identity="owner", hub_uri="ws://localhost:8876")
    (tmp_path / tool.target).write_bytes(b"owner\r\n")
    manage_adapter(tool, root=tmp_path, action="install", block=block)
    installed = (tmp_path / tool.target).read_bytes()
    assert manage_adapter(tool, root=tmp_path, action="install", block=block) == "unchanged"
    assert (tmp_path / tool.target).read_bytes() == installed
    manage_adapter(tool, root=tmp_path, action="uninstall")
    assert (tmp_path / tool.target).read_bytes() == b"owner\r\n"

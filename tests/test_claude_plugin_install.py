# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reversible Claude Code plugin package lifecycle tests
"""Exercise actual plugin installation files and guarded removal."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from synapse_channel.claude_plugin_install import (
    ClaudePluginInstallError,
    apply_plugin,
    inspect_plugin,
    plugin_path,
)


def _binary() -> str:
    resolved = shutil.which("synapse")
    assert resolved is not None
    return resolved


def test_install_upgrade_remove_preserves_unrelated_host_settings(tmp_path: Path) -> None:
    config_root = tmp_path / "claude-profile"
    config_root.mkdir()
    settings = config_root / "settings.json"
    settings.write_text('{"theme":"dark","unrelated":true}\n', encoding="utf-8")
    original = settings.read_bytes()
    token = tmp_path / "token"
    token.write_text("secret-should-never-be-logged\n", encoding="utf-8")
    token.chmod(0o600)

    installed = apply_plugin(
        "install",
        config_root=config_root,
        identity="project/claude",
        uri="ws://localhost:8876",
        token_file=token,
        synapse_bin=_binary(),
    )
    assert installed.state == "owned"
    assert installed.version == "0.1.0"
    plugin = plugin_path(config_root)
    mcp = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    hooks = json.loads((plugin / "hooks/hooks.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["synapse"]["args"][-2:] == ["--token-file", str(token)]
    assert hooks["hooks"]["PreToolUse"][0]["matcher"] == "Edit|Write|Bash"
    assert b"secret-should-never-be-logged" not in b"".join(
        path.read_bytes() for path in plugin.rglob("*") if path.is_file()
    )
    assert settings.read_bytes() == original

    upgraded = apply_plugin(
        "upgrade",
        config_root=config_root,
        identity="project/claude",
        uri="ws://localhost:8877",
        token_file=token,
        synapse_bin=_binary(),
    )
    assert upgraded.state == "owned"
    assert "8877" in (plugin / ".mcp.json").read_text(encoding="utf-8")
    assert settings.read_bytes() == original
    repeated = apply_plugin(
        "upgrade",
        config_root=config_root,
        identity="project/claude",
        uri="ws://localhost:8877",
        token_file=token,
        synapse_bin=_binary(),
    )
    assert repeated.state == "owned"
    removed = apply_plugin("uninstall", config_root=config_root)
    assert removed.state == "absent"
    assert apply_plugin("uninstall", config_root=config_root).state == "absent"
    assert settings.read_bytes() == original
    assert token.read_text(encoding="utf-8").startswith("secret-")


def test_foreign_or_modified_plugin_is_never_replaced_or_removed(tmp_path: Path) -> None:
    config_root = tmp_path / "profile"
    target = plugin_path(config_root)
    target.mkdir(parents=True)
    unknown = target / "owner-file"
    unknown.write_text("keep", encoding="utf-8")
    assert inspect_plugin(config_root).state == "foreign"
    with pytest.raises(ClaudePluginInstallError, match="foreign or modified"):
        apply_plugin("uninstall", config_root=config_root)
    assert unknown.read_text(encoding="utf-8") == "keep"

    shutil.rmtree(target)
    apply_plugin(
        "install",
        config_root=config_root,
        identity="project/claude",
        uri="ws://localhost:8876",
        synapse_bin=_binary(),
    )
    (target / "README.md").write_text("owner edit", encoding="utf-8")
    assert inspect_plugin(config_root).state == "modified"
    with pytest.raises(ClaudePluginInstallError, match="foreign or modified"):
        apply_plugin(
            "upgrade",
            config_root=config_root,
            identity="project/claude",
            uri="ws://localhost:8876",
            synapse_bin=_binary(),
        )
    assert (target / "README.md").read_text(encoding="utf-8") == "owner edit"


def test_unexpected_file_or_symlink_cannot_be_erased(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    apply_plugin(
        "install",
        config_root=root,
        identity="project/claude",
        uri="ws://localhost:8876",
        synapse_bin=_binary(),
    )
    target = plugin_path(root)
    extra = target / "my-data"
    extra.mkdir()
    assert inspect_plugin(root).state == "modified"
    with pytest.raises(ClaudePluginInstallError, match="foreign or modified"):
        apply_plugin("uninstall", config_root=root)
    assert extra.is_dir()
    extra.rmdir()
    marker = target / ".synapse-install.json"
    marker.unlink()
    marker.symlink_to(tmp_path / "private")
    assert inspect_plugin(root).state == "foreign"
    with pytest.raises(ClaudePluginInstallError, match="foreign or modified"):
        apply_plugin(
            "upgrade",
            config_root=root,
            identity="project/claude",
            uri="ws://localhost:8876",
            synapse_bin=_binary(),
        )


def test_host_validation_failure_never_promotes_staged_plugin(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    with pytest.raises(ClaudePluginInstallError, match="rejected the staged"):
        apply_plugin(
            "install",
            config_root=root,
            identity="project/claude",
            uri="ws://localhost:8876",
            synapse_bin=_binary(),
            validate=lambda path: path.is_dir() and False,
        )
    assert inspect_plugin(root).state == "absent"
    assert not list((root / "skills").glob(".synapse-channel-*"))


def test_install_and_upgrade_require_the_correct_ownership_state(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    with pytest.raises(ClaudePluginInstallError, match="absent"):
        apply_plugin(
            "upgrade",
            config_root=root,
            identity="project/claude",
            uri="ws://localhost:8876",
            synapse_bin=_binary(),
        )
    apply_plugin(
        "install",
        config_root=root,
        identity="project/claude",
        uri="ws://localhost:8876",
        synapse_bin=_binary(),
    )
    with pytest.raises(ClaudePluginInstallError, match="already installed"):
        apply_plugin(
            "install",
            config_root=root,
            identity="project/claude",
            uri="ws://localhost:8876",
            synapse_bin=_binary(),
        )
    with pytest.raises(ClaudePluginInstallError, match="action must"):
        apply_plugin("reset", config_root=root)


def test_bad_identity_and_token_file_leave_no_plugin(tmp_path: Path) -> None:
    config_root = tmp_path / "profile"
    with pytest.raises(ClaudePluginInstallError, match="exact project/seat"):
        apply_plugin(
            "install",
            config_root=config_root,
            identity="user",
            uri="ws://localhost:8876",
            synapse_bin=_binary(),
        )
    assert inspect_plugin(config_root).state == "absent"
    with pytest.raises(ClaudePluginInstallError, match="ws"):
        apply_plugin(
            "install",
            config_root=config_root,
            identity="project/claude",
            uri="file:///tmp/hub",
            synapse_bin=_binary(),
        )
    assert inspect_plugin(config_root).state == "absent"
    with pytest.raises(ClaudePluginInstallError, match="without credentials"):
        apply_plugin(
            "install",
            config_root=config_root,
            identity="project/claude",
            uri="ws://secret@localhost:8876",
            synapse_bin=_binary(),
        )
    assert inspect_plugin(config_root).state == "absent"


def test_foreign_target_types_and_marker_versions_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    target = plugin_path(root)
    target.parent.mkdir(parents=True)
    target.write_text("user file", encoding="utf-8")
    assert inspect_plugin(root).state == "foreign"
    target.unlink()
    target.symlink_to(tmp_path / "somewhere")
    assert inspect_plugin(root).state == "foreign"
    target.unlink()
    apply_plugin(
        "install",
        config_root=root,
        identity="project/claude",
        uri="ws://localhost:8876",
        synapse_bin=_binary(),
    )
    marker = target / ".synapse-install.json"
    state = json.loads(marker.read_text(encoding="utf-8"))
    state["schema"] = "other"
    marker.write_text(json.dumps(state), encoding="utf-8")
    assert inspect_plugin(root).state == "foreign"
    state["schema"] = "synapse-claude-plugin-install.v1"
    state["files"] = {}
    marker.write_text(json.dumps(state), encoding="utf-8")
    assert inspect_plugin(root).state == "foreign"


def test_skills_symlink_is_refused_without_writing_outside_profile(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "skills").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ClaudePluginInstallError, match="symlink"):
        apply_plugin(
            "install",
            config_root=root,
            identity="project/claude",
            uri="ws://localhost:8876",
            synapse_bin=_binary(),
        )
    assert list(outside.iterdir()) == []


def test_upgrade_rolls_back_after_a_failed_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "profile"
    apply_plugin(
        "install",
        config_root=root,
        identity="project/claude",
        uri="ws://localhost:8876",
        synapse_bin=_binary(),
    )
    target = plugin_path(root)
    previous = (target / ".mcp.json").read_bytes()
    original_replace = os.replace

    def fail_new_plugin(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        if Path(source).name.startswith(".synapse-channel-") and Path(destination) == target:
            raise OSError("simulated promotion failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_new_plugin)
    with pytest.raises(OSError, match="promotion"):
        apply_plugin(
            "upgrade",
            config_root=root,
            identity="project/claude",
            uri="ws://localhost:8877",
            synapse_bin=_binary(),
        )
    assert inspect_plugin(root).state == "owned"
    assert (target / ".mcp.json").read_bytes() == previous

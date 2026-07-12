# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real filesystem boundary for Kimi hook configuration

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from synapse_channel.kimi_hook_config_file import (
    MAX_KIMI_CONFIG_BYTES,
    ConfigSnapshot,
    HookConfigResult,
    KimiHookConfigFileError,
    install_hook_config,
    read_config_snapshot,
    remove_config_snapshot,
    resolve_kimi_config_path,
    uninstall_hook_config,
    write_config_snapshot,
)
from synapse_channel.kimi_hook_installer import (
    KIMI_HOOK_MARKER_BEGIN,
    KimiHookInstallerError,
)


def _install(path: Path, *, identity: str = "seat/one") -> HookConfigResult:
    return install_hook_config(
        path,
        identity=identity,
        uri="ws://127.0.0.1:8876",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin=sys.executable,
    )


def test_resolve_config_prefers_explicit_override(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.toml"
    resolved = resolve_kimi_config_path(
        str(explicit), environ={"KIMI_CODE_HOME": str(tmp_path / "ignored")}
    )
    assert resolved == explicit


def test_resolve_config_honours_kimi_code_home(tmp_path: Path) -> None:
    root = tmp_path / "kimi-home"
    resolved = resolve_kimi_config_path(None, environ={"KIMI_CODE_HOME": str(root)})
    assert resolved == root / "config.toml"


def test_resolve_config_uses_injected_home_fallback(tmp_path: Path) -> None:
    resolved = resolve_kimi_config_path(None, environ={}, home=tmp_path)
    assert resolved == tmp_path / ".kimi-code" / "config.toml"


def test_read_missing_config_returns_empty_snapshot(tmp_path: Path) -> None:
    assert read_config_snapshot(tmp_path / "missing.toml") == ConfigSnapshot(
        text="", existed=False, fingerprint=None
    )


def test_read_regular_config_captures_text_mode_and_identity(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "x"\n', encoding="utf-8")
    path.chmod(0o640)
    snapshot = read_config_snapshot(path)
    assert snapshot.text == 'model = "x"\n'
    assert snapshot.existed is True
    assert snapshot.fingerprint is not None
    assert snapshot.mode == 0o640


def test_read_rejects_final_component_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.toml"
    target.write_text('model = "x"\n', encoding="utf-8")
    link = tmp_path / "config.toml"
    link.symlink_to(target)
    with pytest.raises(KimiHookConfigFileError, match="regular file"):
        read_config_snapshot(link)


def test_read_rejects_non_regular_file(tmp_path: Path) -> None:
    fifo = tmp_path / "config.toml"
    os.mkfifo(fifo)
    with pytest.raises(KimiHookConfigFileError, match="regular file"):
        read_config_snapshot(fifo)


def test_read_rejects_file_owned_by_another_uid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "x"\n', encoding="utf-8")
    monkeypatch.setattr(
        "synapse_channel.kimi_hook_config_file.os.geteuid", lambda: path.stat().st_uid + 1
    )
    with pytest.raises(KimiHookConfigFileError, match="owned by the current user"):
        read_config_snapshot(path)


def test_read_rejects_oversized_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"x" * (MAX_KIMI_CONFIG_BYTES + 1))
    with pytest.raises(KimiHookConfigFileError, match="automatic-edit limit"):
        read_config_snapshot(path)


def test_read_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"model = \xff\n")
    with pytest.raises(KimiHookConfigFileError, match="not valid UTF-8"):
        read_config_snapshot(path)


def test_read_rejects_file_that_grows_beyond_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "x"\n', encoding="utf-8")

    def oversized_read(_descriptor: int, _count: int) -> bytes:
        return b"x" * (MAX_KIMI_CONFIG_BYTES + 1)

    monkeypatch.setattr("synapse_channel.kimi_hook_config_file.os.read", oversized_read)
    with pytest.raises(KimiHookConfigFileError, match="automatic-edit limit"):
        read_config_snapshot(path)


def test_read_rejects_replacement_between_lstat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    replacement = tmp_path / "replacement.toml"
    path.write_text('model = "old"\n', encoding="utf-8")
    replacement.write_text('model = "new"\n', encoding="utf-8")
    real_open = os.open

    def replace_then_open(target: os.PathLike[str] | str, flags: int) -> int:
        replacement.replace(path)
        return real_open(target, flags)

    monkeypatch.setattr("synapse_channel.kimi_hook_config_file.os.open", replace_then_open)
    with pytest.raises(KimiHookConfigFileError, match="changed while it was being opened"):
        read_config_snapshot(path)


def test_write_new_config_creates_private_file_and_parent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    write_config_snapshot(path, 'model = "x"\n', read_config_snapshot(path))
    assert path.read_text(encoding="utf-8") == 'model = "x"\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_write_existing_config_preserves_mode_and_atomically_replaces(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "old"\n', encoding="utf-8")
    path.chmod(0o640)
    before_inode = path.stat().st_ino
    snapshot = read_config_snapshot(path)
    write_config_snapshot(path, 'model = "new"\n', snapshot)
    assert path.read_text(encoding="utf-8") == 'model = "new"\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert path.stat().st_ino != before_inode


def test_write_rejects_content_over_limit(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    snapshot = read_config_snapshot(path)
    with pytest.raises(KimiHookConfigFileError, match="Updated Kimi config exceeds"):
        write_config_snapshot(path, "x" * (MAX_KIMI_CONFIG_BYTES + 1), snapshot)
    assert not path.exists()


def test_write_rejects_non_directory_parent(tmp_path: Path) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied", encoding="utf-8")
    path = parent / "config.toml"
    with pytest.raises(KimiHookConfigFileError, match="parent is not a directory"):
        write_config_snapshot(path, 'model = "x"\n', ConfigSnapshot("", False, None))


def test_write_refuses_file_that_appeared_after_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    snapshot = read_config_snapshot(path)
    path.write_text('model = "external"\n', encoding="utf-8")
    with pytest.raises(KimiHookConfigFileError, match="appeared during the edit"):
        write_config_snapshot(path, 'model = "ours"\n', snapshot)
    assert path.read_text(encoding="utf-8") == 'model = "external"\n'


def test_write_refuses_changed_snapshot_without_overwriting(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    replacement = tmp_path / "replacement.toml"
    path.write_text('model = "old"\n', encoding="utf-8")
    snapshot = read_config_snapshot(path)
    replacement.write_text('model = "external"\n', encoding="utf-8")
    replacement.replace(path)
    with pytest.raises(KimiHookConfigFileError, match="changed concurrently"):
        write_config_snapshot(path, 'model = "ours"\n', snapshot)
    assert path.read_text(encoding="utf-8") == 'model = "external"\n'


def test_write_refuses_disappeared_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "old"\n', encoding="utf-8")
    snapshot = read_config_snapshot(path)
    path.unlink()
    with pytest.raises(KimiHookConfigFileError, match="disappeared during the edit"):
        write_config_snapshot(path, 'model = "ours"\n', snapshot)
    assert not path.exists()


def test_replace_failure_preserves_original_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "old"\n', encoding="utf-8")
    snapshot = read_config_snapshot(path)

    def fail_replace(_source: os.PathLike[str] | str, _target: os.PathLike[str] | str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("synapse_channel.kimi_hook_config_file.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_config_snapshot(path, 'model = "new"\n', snapshot)
    assert path.read_text(encoding="utf-8") == 'model = "old"\n'
    assert list(tmp_path.iterdir()) == [path]


def test_temp_setup_failure_closes_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"

    def fail_chmod(_descriptor: int, _mode: int) -> None:
        raise OSError("chmod failed")

    monkeypatch.setattr("synapse_channel.kimi_hook_config_file.os.fchmod", fail_chmod)
    with pytest.raises(OSError, match="chmod failed"):
        write_config_snapshot(path, 'model = "x"\n', read_config_snapshot(path))
    assert list(tmp_path.iterdir()) == []


def test_remove_refuses_changed_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    replacement = tmp_path / "replacement.toml"
    path.write_text('model = "old"\n', encoding="utf-8")
    snapshot = read_config_snapshot(path)
    replacement.write_text('model = "external"\n', encoding="utf-8")
    replacement.replace(path)
    with pytest.raises(KimiHookConfigFileError, match="changed concurrently"):
        remove_config_snapshot(path, snapshot)
    assert path.read_text(encoding="utf-8") == 'model = "external"\n'


def test_remove_missing_snapshot_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    remove_config_snapshot(path, read_config_snapshot(path))
    assert not path.exists()


def test_install_missing_config_reports_installed_and_private_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    result = _install(path)
    assert result == HookConfigResult(path, "installed")
    assert KIMI_HOOK_MARKER_BEGIN in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_reinstall_same_hook_is_unchanged_without_replacing_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _install(path)
    before = path.stat()
    result = _install(path)
    after = path.stat()
    assert result == HookConfigResult(path, "unchanged")
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_reinstall_different_identity_reports_updated(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _install(path)
    result = _install(path, identity="seat/two")
    text = path.read_text(encoding="utf-8")
    assert result == HookConfigResult(path, "updated")
    assert "seat/two" in text and "seat/one" not in text


def test_uninstall_preserves_unowned_config_content(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "x"\n', encoding="utf-8")
    _install(path)
    result = uninstall_hook_config(path)
    assert result == HookConfigResult(path, "removed")
    assert path.read_text(encoding="utf-8") == 'model = "x"\n'


def test_uninstall_removes_file_owned_only_by_hook(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _install(path)
    result = uninstall_hook_config(path)
    assert result == HookConfigResult(path, "removed-file")
    assert not path.exists()


def test_uninstall_missing_config_reports_not_installed(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    assert uninstall_hook_config(path) == HookConfigResult(path, "not-installed")


def test_install_invalid_toml_leaves_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    invalid = "this = [is not valid\n"
    path.write_text(invalid, encoding="utf-8")
    with pytest.raises(KimiHookInstallerError, match="not valid TOML"):
        _install(path)
    assert path.read_text(encoding="utf-8") == invalid


def test_install_partial_marker_leaves_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    malformed = f'# {KIMI_HOOK_MARKER_BEGIN}\nmodel = "x"\n'
    path.write_text(malformed, encoding="utf-8")
    with pytest.raises(KimiHookInstallerError, match="partial, duplicated, or misordered"):
        _install(path)
    assert path.read_text(encoding="utf-8") == malformed

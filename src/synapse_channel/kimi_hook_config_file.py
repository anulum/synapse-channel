# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded atomic file boundary for Kimi hook configuration
"""Safely install, replace, or remove Kimi's user-owned hook configuration.

The marker planner stays pure. This module owns the sensitive filesystem
transaction: bounded UTF-8 reads, final-component symlink rejection, owner checks,
a stable snapshot fingerprint, same-directory atomic replacement, mode
preservation, and directory fsync. Changes detected before replacement fail rather
than being overwritten.
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from synapse_channel.core.errors import SynapseError
from synapse_channel.kimi_hook_installer import (
    contains_hook_block,
    plan_install_hook,
    plan_uninstall_hook,
    render_marked_hook_block,
)

MAX_KIMI_CONFIG_BYTES = 1_048_576
"""Maximum Kimi config size accepted for an automatic edit (one MiB)."""

_Fingerprint = tuple[int, int, int, int, int]
HookConfigOutcome = Literal[
    "installed",
    "updated",
    "unchanged",
    "removed",
    "removed-file",
    "not-installed",
]


class KimiHookConfigFileError(SynapseError, ValueError):
    """The Kimi config path or snapshot is unsafe to mutate."""

    code = "kimi_hook_config_file"


@dataclass(frozen=True)
class ConfigSnapshot:
    """One bounded config read plus the identity required for compare-before-write."""

    text: str
    existed: bool
    fingerprint: _Fingerprint | None
    mode: int = 0o600


@dataclass(frozen=True)
class HookConfigResult:
    """Outcome of one compare-before-write Kimi hook configuration transaction."""

    path: Path
    outcome: HookConfigOutcome


def _fingerprint(info: os.stat_result) -> _Fingerprint:
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)


def resolve_kimi_config_path(
    override: str | None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve an override or ``$KIMI_CODE_HOME/config.toml`` without following it."""
    values = os.environ if environ is None else environ
    if override:
        candidate = Path(override).expanduser()
    else:
        configured_home = values.get("KIMI_CODE_HOME", "").strip()
        root = (
            Path(configured_home).expanduser()
            if configured_home
            else (home or Path.home()) / ".kimi-code"
        )
        candidate = root / "config.toml"
    return Path(os.path.abspath(candidate))


def _validate_regular_owner(path: Path, info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise KimiHookConfigFileError(f"Kimi config must be a regular file: {path}")
    if info.st_uid != os.geteuid():
        raise KimiHookConfigFileError(f"Kimi config must be owned by the current user: {path}")
    if info.st_size > MAX_KIMI_CONFIG_BYTES:
        raise KimiHookConfigFileError(
            f"Kimi config exceeds the {MAX_KIMI_CONFIG_BYTES}-byte automatic-edit limit."
        )


def read_config_snapshot(path: Path) -> ConfigSnapshot:
    """Read one owner-controlled regular file without following its final symlink."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return ConfigSnapshot(text="", existed=False, fingerprint=None)
    _validate_regular_owner(path, before)

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        after = os.fstat(descriptor)
        _validate_regular_owner(path, after)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise KimiHookConfigFileError("Kimi config changed while it was being opened.")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, MAX_KIMI_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_KIMI_CONFIG_BYTES:
                raise KimiHookConfigFileError(
                    f"Kimi config exceeds the {MAX_KIMI_CONFIG_BYTES}-byte automatic-edit limit."
                )
    finally:
        os.close(descriptor)
    try:
        text = b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise KimiHookConfigFileError(
            "Kimi config is not valid UTF-8; refusing to edit it."
        ) from exc
    return ConfigSnapshot(
        text=text,
        existed=True,
        fingerprint=_fingerprint(after),
        mode=stat.S_IMODE(after.st_mode),
    )


def _assert_snapshot_current(path: Path, snapshot: ConfigSnapshot) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        if snapshot.existed:
            raise KimiHookConfigFileError("Kimi config disappeared during the edit.") from None
        return
    if not snapshot.existed:
        raise KimiHookConfigFileError(
            "Kimi config appeared during the edit; refusing to overwrite it."
        )
    _validate_regular_owner(path, current)
    if snapshot.fingerprint != _fingerprint(current):
        raise KimiHookConfigFileError("Kimi config changed concurrently; no update was written.")


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_config_snapshot(path: Path, text: str, snapshot: ConfigSnapshot) -> None:
    """Atomically replace ``path`` only when ``snapshot`` is still current."""
    data = text.encode("utf-8")
    if len(data) > MAX_KIMI_CONFIG_BYTES:
        raise KimiHookConfigFileError(
            f"Updated Kimi config exceeds the {MAX_KIMI_CONFIG_BYTES}-byte limit."
        )
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise KimiHookConfigFileError(
            f"Kimi config parent is not a directory: {path.parent}"
        ) from exc
    _assert_snapshot_current(path, snapshot)

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, snapshot.mode if snapshot.existed else 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_snapshot_current(path, snapshot)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def remove_config_snapshot(path: Path, snapshot: ConfigSnapshot) -> None:
    """Remove ``path`` only when the captured file identity is still current."""
    if not snapshot.existed:
        return
    _assert_snapshot_current(path, snapshot)
    path.unlink()
    _fsync_directory(path.parent)


def install_hook_config(
    path: Path,
    *,
    identity: str,
    uri: str,
    ready_timeout: float,
    token_file: str | None,
    synapse_bin: str | None,
) -> HookConfigResult:
    """Install or replace the marked Synapse hook block in ``path`` atomically."""
    snapshot = read_config_snapshot(path)
    had_block = contains_hook_block(snapshot.text)
    block = render_marked_hook_block(
        identity=identity,
        uri=uri,
        ready_timeout=ready_timeout,
        token_file=token_file,
        synapse_bin=synapse_bin,
    )
    content = plan_install_hook(snapshot.text, block)
    if content == snapshot.text:
        return HookConfigResult(path, "unchanged")
    write_config_snapshot(path, content, snapshot)
    return HookConfigResult(path, "updated" if had_block else "installed")


def uninstall_hook_config(path: Path) -> HookConfigResult:
    """Remove only the marked Synapse hook block from ``path`` atomically."""
    snapshot = read_config_snapshot(path)
    if not snapshot.existed or not contains_hook_block(snapshot.text):
        return HookConfigResult(path, "not-installed")
    content = plan_uninstall_hook(snapshot.text)
    if content.strip():
        write_config_snapshot(path, content, snapshot)
        return HookConfigResult(path, "removed")
    remove_config_snapshot(path, snapshot)
    return HookConfigResult(path, "removed-file")

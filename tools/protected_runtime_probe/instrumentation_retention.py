# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — readonly retained recording dataset exposure
"""Expose only a complete sealed recording set and record its exact mount custody."""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

from synapse_channel.core.protected_write_mounts import parse_protected_writer_mounts

from . import require_native_linux
from .instrumentation import RECORDING_ROLES
from .instrumentation_recording import MAX_RECORDING_BYTES
from .storage import bind_readonly, open_directory, parse_descriptor_mount_id, unbind

_PARENT = Path("/opt/synapse-channel/protected-runtime-qualification-evidence")


def _mount_id(descriptor: int) -> int:
    """Read only the actual retained descriptor's bounded kernel mount observation."""
    with Path(f"/proc/self/fdinfo/{descriptor}").open("r", encoding="ascii") as source:
        text = source.read(4097)
    if len(text) > 4096:
        raise ValueError("recording mount descriptor observation exceeds its bound")
    return parse_descriptor_mount_id(text)


def _describe_recording_bind(directory: int, target: Path) -> dict[str, object]:
    """Compare actual descriptor, inode and readonly data-only mount observations."""
    original = os.fstat(directory)
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        observed = os.fstat(descriptor)
        mounted = _mount_id(descriptor)
        origin = _mount_id(directory)
        with Path("/proc/self/mountinfo").open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("recording mount table observation exceeds its bound")
        matches = [
            row
            for row in parse_protected_writer_mounts(raw)
            if row.mount_id == mounted and row.target == str(target)
        ]
        if (
            (observed.st_dev, observed.st_ino) != (original.st_dev, original.st_ino)
            or mounted == origin
            or len(matches) != 1
            or not {"ro", "nosuid", "nodev", "noexec"} <= matches[0].options
        ):
            raise ValueError("sealed recording exposure is not its exact data-only readonly bind")
        return {
            "target": str(target),
            "source": os.readlink(f"/proc/self/fd/{directory}"),
            "source_device": original.st_dev,
            "source_inode": original.st_ino,
            "source_mount_id": origin,
            "mount_id": mounted,
            "mount_root": matches[0].root,
            "readonly": True,
            "nosuid": True,
            "nodev": True,
            "noexec": True,
        }
    finally:
        os.close(descriptor)


def publish_recording_outputs(directory: int, run_id: str) -> dict[str, object]:
    """Expose one complete root-sealed dataset directory for ordinary-user analysis.

    Parameters
    ----------
    directory:
        Borrowed root-owned 0555 directory containing exactly six readonly
        dataset copies plus the root-generated manifest.json.
    run_id:
        Fresh exact finite campaign identifier selected by the root bootstrap.

    Returns
    -------
    dict[str, object]
        Independently observed readonly mount binding and owner-only retirement steps.

    Raises
    ------
    PermissionError
        Unless actual real/effective/saved UID-GID values are all root.
    ValueError, OSError
        On an incomplete directory, unsafe metadata or changed/missing mount custody.

    Notes
    -----
    Parses no child database. Publication grants no execution or deletion.
    Only this new exact bind is detached on a publication failure; the sealed
    backing remains for the failed campaign's explicit recovery report.
    """
    require_native_linux(sys.platform, operation="recording dataset exposure")
    if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise PermissionError("recording exposure requires the actual root controller")
    if re.fullmatch("run-[0-9a-f]{16}", run_id) is None:
        raise ValueError("invalid recording exposure run identifier")
    _require_sealed_inventory(directory)
    parent = open_directory(_PARENT, trusted=True, create=True)
    try:
        os.mkdir(run_id, 0o755, dir_fd=parent)
    finally:
        os.close(parent)
    target = _PARENT / run_id
    mounted = False
    try:
        bind_readonly(directory, target, executable=False)
        mounted = True
        binding = _describe_recording_bind(directory, target)
        return {
            "dataset_bind": binding,
            "retirement": [
                "Owner only: verify report, writer quiescence and backing identity.",
                "Open the exact dataset target without following links; compare mount ID, "
                "source device/inode, source path and data-only readonly options with this record.",
                "Unmount only that verified dataset bind; verify its mount ID is absent, "
                "then remove the exact root-owned directory placeholder.",
                "Retire the report bind separately under its recorded identity procedure; "
                "only afterward may the exact backing tree be retired. Keep all role accounts.",
            ],
        }
    except BaseException:
        if mounted:
            original = os.fstat(directory)
            unbind(target, expected_identity=(original.st_dev, original.st_ino))
        target.rmdir()
        raise


def _require_sealed_inventory(directory: int) -> None:
    """Require the exact root-generated sealed set before creating an exposure."""
    info = os.fstat(directory)
    names = {role + ".sqlite" for role in RECORDING_ROLES} | {"manifest.json"}
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid) != (0, 0)
        or stat.S_IMODE(info.st_mode) != 0o555
        or set(os.listdir(directory)) != names
    ):
        raise ValueError("recording exposure requires the complete root-sealed directory")
    for name in names:
        leaf = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(leaf.st_mode)
            or leaf.st_nlink != 1
            or (leaf.st_uid, leaf.st_gid) != (0, 0)
            or stat.S_IMODE(leaf.st_mode) != 0o444
            or not 0
            < leaf.st_size
            <= (2 * 1024 * 1024 if name == "manifest.json" else MAX_RECORDING_BYTES)
        ):
            raise ValueError("recording exposure contains an unsealed entry")

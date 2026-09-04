# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — atomic exact-seat active-waker lifecycle lock
"""Serialise active-waker configuration and service effects across CLI processes."""

from __future__ import annotations

import contextlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.waker_config import waker_config_dir, waker_config_path


class WakerLockError(ValueError):
    """Raised when an exact-seat lifecycle mutation cannot be serialized."""


@contextmanager
def waker_control_lock(identity: str, *, home: Path | None = None) -> Iterator[None]:
    """Hold a non-blocking, owner-only lock for one exact waker identity."""
    ensure_private_dir(
        waker_config_dir(home=home),
        parents=True,
        purpose="waker configuration directory",
    )
    path = waker_config_path(identity, home=home).with_suffix(".lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise OSError("unsafe waker control lock")
        os.fchmod(descriptor, 0o600)
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError) as exc:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise WakerLockError(
            f"waker lifecycle is already changing or lock is unsafe: {path}"
        ) from exc
    try:
        yield
    finally:
        os.close(descriptor)

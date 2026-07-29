#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — fail release preflight on incomplete Linux security APIs
"""Require the interpreter APIs that exercise Linux sealed MCP launch coverage."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform boundary.
    fcntl = None  # type: ignore[assignment]

REQUIRED_SEAL_CONSTANTS = (
    "F_ADD_SEALS",
    "F_SEAL_WRITE",
    "F_SEAL_GROW",
    "F_SEAL_SHRINK",
    "F_SEAL_SEAL",
)


def missing_linux_sealed_launch_caps(
    *,
    platform: str = sys.platform,
    os_module: ModuleType = os,
    fcntl_module: ModuleType | None = fcntl,
    proc_fd: Path = Path("/proc/self/fd"),
) -> tuple[str, ...]:
    """Return missing sealed-launch capabilities for a Linux interpreter."""
    if not platform.startswith("linux"):
        return ()
    missing: list[str] = []
    for name in ("O_NOFOLLOW", "geteuid", "memfd_create", "MFD_ALLOW_SEALING"):
        if not hasattr(os_module, name):
            missing.append(f"os.{name}")
    if not proc_fd.is_dir():
        missing.append(str(proc_fd))
    if fcntl_module is None:
        missing.append("fcntl")
    else:
        missing.extend(
            f"fcntl.{name}" for name in REQUIRED_SEAL_CONSTANTS if not hasattr(fcntl_module, name)
        )
    return tuple(missing)


def main(argv: Sequence[str] | None = None) -> int:
    """Print one bounded capability verdict and return nonzero on Linux drift."""
    del argv
    missing = missing_linux_sealed_launch_caps()
    if missing:
        print(
            "Linux sealed MCP launch coverage unavailable in this interpreter: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    if sys.platform.startswith("linux"):
        print("Linux sealed MCP launch capabilities present")
    else:
        print("non-Linux platform: sealed MCP launch capability gate not applicable")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI boundary.
    raise SystemExit(main())

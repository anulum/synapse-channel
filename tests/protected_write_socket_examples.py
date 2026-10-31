# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained actual Unix endpoint test directories
"""Keep actual test socket inodes reachable by bounded Linux path spellings."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def private_unix_directory(tmp_path: Path) -> Iterator[Path]:
    """Retain a same-UID diagnostic endpoint directory until owned children join."""
    directory = tmp_path / "endpoint"
    directory.mkdir(mode=0o700)
    if sys.platform != "linux":
        yield directory
        return
    anchor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        # An explicit coordinator PID works in both the current process and
        # spawned diagnostic peers, without exporting the anchor FD to them.
        yield Path(f"/proc/{os.getpid()}/fd/{anchor}") / "endpoint"
    finally:
        os.close(anchor)

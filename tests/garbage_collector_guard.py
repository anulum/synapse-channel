# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - refuse tests that reconfigure the garbage collector
"""Fail a test that leaves the garbage collector configured differently.

Every test shares one interpreter. A test that raises the collection thresholds
or disables collection changes the pauses of every later test, which then fails
far from the cause. The shared conftest wraps each test in this guard.
"""

from __future__ import annotations

import contextlib
import gc
from collections.abc import Iterator

import pytest


def _configuration() -> tuple[tuple[int, int, int], bool]:
    """Return the collection thresholds and whether collection is enabled."""
    return gc.get_threshold(), gc.isenabled()


@contextlib.contextmanager
def garbage_collector_unchanged() -> Iterator[None]:
    """Require the guarded block to leave the collector as it found it.

    Yields
    ------
    None
        Control to the guarded test.

    Raises
    ------
    pytest.fail.Exception
        When the block returns with other thresholds or another enabled
        state. The original configuration is restored first, so later tests
        run unaffected and only the test that changed it fails.
    """
    before = _configuration()
    yield
    after = _configuration()
    if after != before:
        gc.set_threshold(*before[0])
        if before[1]:
            gc.enable()
        else:
            gc.disable()
        pytest.fail(
            f"the test changed the garbage collector from {before} to {after}; "
            "run the code that changes it in a child process"
        )

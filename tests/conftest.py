# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared pytest fixtures

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _restore_synapse_logger() -> Iterator[None]:
    """Snapshot and restore the ``synapse`` logger around every test.

    ``configure_logging`` (exercised by the hub/worker CLI tests) mutates the
    process-global ``synapse`` logger — its handlers, level, and propagation. Without
    this guard that state leaks across test files and breaks ``caplog`` capture, which
    relies on propagation, in a later test that asserts on a ``synapse.*`` log record.
    """
    logger = logging.getLogger("synapse")
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    saved_propagate = logger.propagate
    try:
        yield
    finally:
        logger.handlers[:] = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared pytest fixtures

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_machine_identity(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point ``XDG_DATA_HOME`` at a session-scoped temporary directory.

    :class:`~synapse_channel.client.agent.SynapseAgent` presents the machine
    identity by default, and the key resolves through ``$XDG_DATA_HOME``. Left
    unset, every test agent would read — or first-use provision — the
    developer's real ``~/.local/share/synapse`` key: non-hermetic, and a test
    hub would pin real-machine key ids. One session-scoped directory keeps the
    whole run (including subprocess end-to-end tests, which inherit the
    environment) on a throwaway key while still exercising the real
    provisioning and signing paths. Tests that need a specific data home keep
    overriding per-test with ``monkeypatch.setenv``.
    """
    data_home = tmp_path_factory.mktemp("machine-identity-data-home")
    previous = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = str(data_home)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = previous


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

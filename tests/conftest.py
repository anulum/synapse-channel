# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared pytest fixtures

from __future__ import annotations

import contextlib
import functools
import importlib.util
import logging
import os
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub
    from synapse_channel.core.persistence import EventStore

_HANG_IDENTITY_NAME = "synapse_pytest_hang_identity"
_HANG_IDENTITY_PLUGIN = Path(__file__).resolve().parents[1] / "tools" / "pytest_hang_identity.py"
_TESTS_DIR = str(Path(__file__).resolve().parent) + os.sep
_TEST_OWNED_HUBS: list[SynapseHub] = []
"""Hubs the running test constructed itself, in creation order."""
_TEST_OWNED_STORES: list[EventStore] = []
"""Event stores the running test constructed itself, in creation order."""


def _release_errors() -> tuple[type[BaseException], ...]:
    """Return the errors a late close may raise: a still-bound hub or a closed database."""
    errors: list[type[BaseException]] = [RuntimeError, sqlite3.Error]
    with contextlib.suppress(ImportError):
        from sqlcipher3 import dbapi2  # optional encrypted-store driver

        errors.append(dbapi2.Error)
    return tuple(errors)


def _record_test_owned(init: Callable[..., None], owned: list[Any]) -> Callable[..., None]:
    """Wrap ``__init__`` so an instance built directly by test code is added to ``owned``.

    Only the frame that called the constructor is inspected. An object that
    product code builds (even while a test is running) is not recorded, so a
    leak in ``src`` still surfaces as a ResourceWarning.
    """

    @functools.wraps(init)
    def wrapped(self: object, *args: Any, **kwargs: Any) -> None:
        init(self, *args, **kwargs)
        caller = sys._getframe(1).f_code.co_filename
        if caller.startswith(_TESTS_DIR):
            owned.append(self)

    return wrapped


def _hang_identity_module() -> ModuleType:
    """Load the hang-identity watcher from its source file.

    The pytest console script does not put the repository root on ``sys.path``.
    Importing the watcher as a package under ``tools`` therefore fails before
    collection. The file path is relative to this conftest and does not depend
    on ``PYTHONPATH``.
    """
    spec = importlib.util.spec_from_file_location(_HANG_IDENTITY_NAME, _HANG_IDENTITY_PLUGIN)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the hang-identity plugin from {_HANG_IDENTITY_PLUGIN}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_HANG_IDENTITY_NAME] = module
    spec.loader.exec_module(module)
    return module


def pytest_configure(config: pytest.Config) -> None:
    """Register the hang-identity watcher for every caller of this conftest."""
    if config.pluginmanager.has_plugin(_HANG_IDENTITY_NAME):
        return
    config.pluginmanager.register(_hang_identity_module(), _HANG_IDENTITY_NAME)


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


@pytest.fixture(autouse=True, scope="session")
def _track_test_owned_stores() -> Iterator[None]:
    """Record the hubs and event stores test code constructs, for the whole session.

    A contract-only CI lane runs a few tests without installing the package. No
    test there can build a hub or a store, so there is nothing to record.
    """
    if importlib.util.find_spec("synapse_channel") is None:
        yield
        return
    from synapse_channel.core.hub import SynapseHub
    from synapse_channel.core.persistence import EventStore

    patcher = pytest.MonkeyPatch()
    patcher.setattr(
        SynapseHub, "__init__", _record_test_owned(SynapseHub.__init__, _TEST_OWNED_HUBS)
    )
    patcher.setattr(
        EventStore, "__init__", _record_test_owned(EventStore.__init__, _TEST_OWNED_STORES)
    )
    try:
        yield
    finally:
        patcher.undo()


@pytest.fixture(autouse=True)
def _release_test_owned_stores() -> Iterator[None]:
    """Close the hubs and event stores a test built itself and left open.

    Python 3.13 reports an unclosed SQLite connection when the garbage collector
    reaches it, which is usually during a later, unrelated test. Releasing test-
    owned objects at the end of the test that made them keeps that report out of
    other tests. Hubs close first: a hub anchors its checkpoint against its
    journal while closing.
    """
    _TEST_OWNED_HUBS.clear()
    _TEST_OWNED_STORES.clear()
    try:
        yield
    finally:
        releasable: list[SynapseHub | EventStore] = [*_TEST_OWNED_HUBS, *_TEST_OWNED_STORES]
        _TEST_OWNED_HUBS.clear()
        _TEST_OWNED_STORES.clear()
        for item in releasable:
            with contextlib.suppress(*_release_errors()):
                item.close()

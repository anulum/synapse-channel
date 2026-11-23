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
import socket
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest

from garbage_collector_guard import garbage_collector_unchanged

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
_SEAT_ENVIRONMENT_VARIABLES: tuple[str, ...] = (
    "SYN_INBOX_SOURCE",
    "SYN_HOME",
    "SYN_IDENTITY",
    "SYN_PROJECT",
    "SYN_TMUX_PROVIDER",
    "SYNAPSE_WAITER_PIDFILE",
    "SYNAPSE_AUTO_CONNECT",
)
"""Ambient seat-shell variables cleared for the duration of a test session."""


def _reserve_closed_hub() -> tuple[socket.socket, str]:
    """Bind one loopback port and leave it unable to accept a connection.

    On Linux and Windows a bound socket that is not listening refuses
    connections, and holding it stops another process from opening that port
    for the rest of the run. A BSD stack (macOS) drops a connection attempt to
    a bound socket that is not listening, so the client would wait out its
    timeout; there the socket is closed again and the unbound port refuses.
    Port 0 is not usable: a WebSocket client can map it to 80.
    """
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 0))
    port = int(held.getsockname()[1])
    if port in (0, 8876):
        held.close()
        raise RuntimeError("the closed hub port is not usable")
    if sys.platform == "darwin":
        held.close()
    return held, f"ws://127.0.0.1:{port}"


_CLOSED_HUB_SOCKET, CLOSED_HUB_URI = _reserve_closed_hub()
# Set before collection. Parser defaults call ``default_hub_uri()`` at import.
os.environ["SYNAPSE_URI"] = CLOSED_HUB_URI


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
def _closed_default_hub() -> Iterator[str]:
    """Point every test at a closed hub so none can reach one already running.

    The CLI reads ``SYNAPSE_URI`` when ``--uri`` is omitted, including the
    inbox path that otherwise falls back to the workstation port. A test that
    needs the built-in default unsets the variable. The socket stays open
    until the session ends.
    """
    os.environ["SYNAPSE_URI"] = CLOSED_HUB_URI
    try:
        yield CLOSED_HUB_URI
    finally:
        _CLOSED_HUB_SOCKET.close()


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


@pytest.fixture(autouse=True, scope="session")
def _isolated_state_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point ``XDG_STATE_HOME`` at a session-scoped temporary directory.

    Launchers write the session ledger and launch records under it. Left unset,
    a test that starts an agent would write into the developer's real
    ``~/.local/state/synapse``. Subprocesses inherit it, as with the data home.
    """
    state_home = tmp_path_factory.mktemp("state-home")
    previous = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = str(state_home)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = previous


@pytest.fixture(autouse=True, scope="session")
def _isolated_seat_environment() -> Iterator[None]:
    """Remove ambient seat-shell variables for the duration of the session.

    An active operator or agent seat shell exports coordination variables that
    product code reads directly when explicit arguments are omitted:

    * ``SYN_INBOX_SOURCE`` — :meth:`~synapse_channel.mcp.inbox_queries._McpInboxQueries.__init__`
      and :mod:`synapse_channel.ergonomics_inbox`;
    * ``SYN_HOME`` — :meth:`~synapse_channel.mcp.inbox_queries._McpInboxQueries.__init__`,
      :mod:`synapse_channel.mcp.inbox`, :mod:`synapse_channel.mcp.native_session`,
      and :mod:`synapse_channel.ergonomics`;
    * ``SYN_IDENTITY`` and ``SYN_PROJECT`` — :mod:`synapse_channel.ergonomics`,
      :mod:`synapse_channel.cli_status`, :mod:`synapse_channel.cli_arm`,
      :mod:`synapse_channel.cli_label`, :mod:`synapse_channel.mcp.onboarding`,
      :mod:`synapse_channel.git.claim_check_context`, :mod:`synapse_channel.agent_tmux`,
      :mod:`synapse_channel.opencode_composer`, :mod:`synapse_channel.seat_restore`,
      :mod:`synapse_channel.session_ledger_collect`, and
      :mod:`synapse_channel.shell_integration`;
    * ``SYN_TMUX_PROVIDER``, ``SYNAPSE_WAITER_PIDFILE``, and
      ``SYNAPSE_AUTO_CONNECT`` — :mod:`synapse_channel.shell_integration`,
      :mod:`synapse_channel.worker_session`, :mod:`synapse_channel.agent_tmux`,
      and :mod:`synapse_channel.seat_restore`.

    Clearing only these named seat variables leaves CI test options such as
    ``SYNAPSE_TEST_*`` and ``SYNAPSE_FUZZ_*`` intact. Subprocess tests inherit
    the cleaned environment, and tests of environment resolution keep setting
    variables per test with ``monkeypatch.setenv``. Prior values are restored
    at session end.
    """
    previous = {name: os.environ.pop(name, None) for name in _SEAT_ENVIRONMENT_VARIABLES}
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


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


@pytest.fixture(autouse=True)
def _keep_garbage_collector_configuration() -> Iterator[None]:
    """Fail the test that leaves the garbage collector reconfigured.

    In-process ``mypy.api.run`` once raised the thresholds for the rest of the
    session, and the resulting multi-second collections broke unrelated hub
    tests much later. The guard names the test that changed the collector.
    """
    with garbage_collector_unchanged():
        yield


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

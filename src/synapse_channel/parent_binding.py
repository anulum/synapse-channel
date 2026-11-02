# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — end a child waiter together with the process that started it
"""Tie a long-lived child process to its parent so it never outlives it.

A pane bridge keeps one ``synapse wait`` child connected for a long time. If the
bridge dies, that child must not stay connected and keep advertising a pane
receiver that nobody serves. On Linux the kernel delivers ``SIGTERM`` to the
child when its parent exits (``PR_SET_PDEATHSIG``); on every platform the child
also compares its current parent with the expected one, so an orphan notices
within one poll interval even without the kernel signal.
"""

from __future__ import annotations

import ctypes
import logging
import os
import signal
import sys

_PR_SET_PDEATHSIG = 1
"""``prctl`` option that asks the kernel to signal this process when its parent exits."""

_LOG = logging.getLogger(__name__)


def bind_to_parent(parent_pid: int | None, *, signal_number: int = signal.SIGTERM) -> bool:
    """Ask to be stopped with ``parent_pid`` and report whether it is still the parent.

    Parameters
    ----------
    parent_pid : int or None
        Process that started this one. ``None`` means no binding was requested.
    signal_number : int, optional
        Signal the kernel sends on the parent's exit (Linux only). A refused
        request is logged; the parent check in the caller's loop still bounds
        how long an orphan can live.

    Returns
    -------
    bool
        ``True`` when no binding was requested or ``parent_pid`` is still the
        parent. ``False`` when the parent already changed, for example because it
        exited before this call; the caller must then stop at once.
    """
    if parent_pid is None:
        return True
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_PDEATHSIG, signal_number, 0, 0, 0) != 0:
            _LOG.warning(
                "parent-death signal not set (errno %d); relying on the parent check",
                ctypes.get_errno(),
            )
    return parent_alive(parent_pid)


def parent_alive(parent_pid: int | None) -> bool:
    """Return whether ``parent_pid`` is still this process's parent.

    Parameters
    ----------
    parent_pid : int or None
        Expected parent. ``None`` means no binding was requested.

    Returns
    -------
    bool
        ``True`` when no binding was requested or the parent is unchanged.
    """
    return parent_pid is None or os.getppid() == parent_pid

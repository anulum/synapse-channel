# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a process tree does not count one process twice
"""A parent id that points back into its own set is listed once."""

from __future__ import annotations

import os
import time

import synapse_channel.host_sessions_accounting as accounting
from synapse_channel.host_sessions_accounting import _linux_tree, _windows_tree, account_process


def test_a_cycle_lists_each_process_once() -> None:
    """Two processes that name each other are not walked until the cap."""
    this = os.getpid()
    other = this + 1
    windows = _windows_tree(this, {other: (this, "child.exe"), this: (other, "parent.exe")})
    assert windows.count(this) == 1
    assert windows.count(other) == 1
    assert set(windows) == {this, other}

    saved = accounting._CHILDREN
    accounting._CHILDREN = (time.monotonic() + 30.0, {this: [other], other: [this]})
    try:
        linux = _linux_tree(this)
        observed = account_process(this)
    finally:
        accounting._CHILDREN = saved
    assert linux.count(this) == 1 and linux.count(other) == 1
    assert set(linux) == {this, other}
    pids = observed["tree_pids"]
    assert isinstance(pids, list)
    assert len(pids) == len(set(pids))
    assert observed["requested_pid"] == this

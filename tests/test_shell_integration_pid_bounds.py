# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — liveness probe never treats a non-positive pid as a process
"""Zero and negative pids name process groups for kill(2); none is reported as live."""

from __future__ import annotations

import os
import platform

import pytest

from synapse_channel.shell_integration import pid_is_live_process


@pytest.mark.parametrize("pid", [0, -1, -os.getpid()])
def test_a_non_positive_pid_is_never_a_live_process(pid: int) -> None:
    """A group or broadcast pid is never mistaken for one live process."""
    assert pid_is_live_process(pid) is False


@pytest.mark.skipif(platform.system() != "Linux", reason="Requires Linux /proc process identity")
def test_this_process_is_live() -> None:
    """The control: the probe sees its own running process."""
    assert pid_is_live_process(os.getpid()) is True

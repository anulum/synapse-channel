# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded retained process subtree termination
"""Terminate one exact retained Linux process subtree and observe recursive emptiness."""

from __future__ import annotations

import math
import os
import time

from synapse_channel.core.protected_write_subtree import (
    ProtectedWriterSubtreeObservation,
    ProtectedWriterSubtreeStateChanged,
    inspect_protected_writer_subtree,
)


def stop_protected_writer_subtree(
    directory_fd: int,
    *,
    expected_identity: tuple[int, int],
    timeout_seconds: float,
) -> ProtectedWriterSubtreeObservation:
    """Kill all processes in an exact retained subtree with a bounded empty-state wait.

    Parameters
    ----------
    directory_fd:
        Supervisor-retained kernel cgroup v2 directory. The supervisor must have
        independently bound launch membership and migration controls before use.
    expected_identity:
        Operator-enrolled device/inode identity; never resolved from a client path.
    timeout_seconds:
        Finite positive deadline up to 30 seconds, covering verification and wait.

    Returns
    -------
    ProtectedWriterSubtreeObservation
        Actual recursive populated0 observation after cgroup.kill. It describes
        processes only; root-controlled custody and no descriptor escape remain
        required before recovery or a positive settlement receipt.

    Raises
    ------
    ValueError
        On invalid budget/identity, another group, unsafe kernel data or threaded type.
    TimeoutError
        If emptiness cannot be observed inside the deadline, even if kill succeeded.
    OSError
        On inaccessible/removed kernel group, unavailable cgroup.kill or denied writes.

    Notes
    -----
    Only the supervisor should call this mutating operation. Kernel permissions
    determine who may signal the actual group. This API grants no authority,
    creates no enrollment, and never releases files, claims or reservation custody.
    The supervisor must exclude new admissions into the group throughout stop
    and recovery and retain target descriptors independently. Valid kernel event
    transitions are sampled again within the same deadline; malformed data or
    changes to retained metadata are never retried.
    """
    if (
        type(timeout_seconds) not in {int, float}
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 30
    ):
        raise ValueError("invalid writer subtree stop deadline")
    if (
        type(expected_identity) is not tuple
        or len(expected_identity) != 2
        or any(type(value) is not int or value <= 0 for value in expected_identity)
    ):
        raise ValueError("invalid writer subtree stop identity")
    deadline = time.monotonic() + timeout_seconds
    retained = os.dup(directory_fd)
    try:
        before = _stable_observation(retained, deadline)
        if (before.device, before.inode) != expected_identity:
            raise ValueError("writer subtree stop identity differs from enrollment")
        if before.controls[0].kind != "domain":
            raise ValueError("writer subtree stop requires an ordinary domain")
        control = os.open("cgroup.kill", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=retained)
        try:
            os.write(control, b"1")
        finally:
            os.close(control)
        while True:
            observed = _stable_observation(retained, deadline)
            if not observed.populated:
                return observed
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    finally:
        os.close(retained)


def _stable_observation(directory_fd: int, deadline: float) -> ProtectedWriterSubtreeObservation:
    """Retry only valid event transitions within the original verification deadline."""
    while time.monotonic() < deadline:
        try:
            observation = inspect_protected_writer_subtree(directory_fd)
        except ProtectedWriterSubtreeStateChanged:
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        else:
            if time.monotonic() < deadline:
                return observation
    raise TimeoutError("writer subtree stop deadline expired")

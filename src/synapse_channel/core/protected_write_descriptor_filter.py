# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — writer descriptor message syscall restriction
"""Install an irreversible native writer restriction on ancillary descriptor routes."""

from __future__ import annotations

import ctypes
import errno
import os
import sys


class _Filter(ctypes.Structure):
    """Linux classic BPF instruction layout from linux/filter.h."""

    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _Program(ctypes.Structure):
    """Linux sock_fprog layout retaining its instruction array during loading."""

    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_Filter))]


def install_protected_writer_descriptor_filter(*, expected_abi: str) -> None:
    """Irreversibly block native ancillary-message and io_uring syscalls.

    Parameters
    ----------
    expected_abi : str
        Independently enrolled ABI. This pilot implements linux-x86_64 only;
        enrollment, actual kernel architecture and 64-bit process ABI must
        match before load.

    Raises
    ------
    ValueError
        On unsupported or mismatched enrolled/native ABI before any mutation.
    OSError
        If no-new-privileges or thread-synchronized filter installation fails.
        Failure may leave no-new-privileges set; the launch must abort.
        A divergent existing thread filter fails synchronization with EBUSY.

    Notes
    -----
    Call only in the disposable writer, after trusted launch has closed all
    unapproved descriptors and before target access or protocol dispatch. Sets
    PR_SET_NO_NEW_PRIVS and synchronizes the filter across existing threads;
    descendants inherit it. Native sendmsg/recvmsg, sendmmsg/recvmmsg and all
    three io_uring entrypoints and pidfd_getfd return EPERM. Non-native audit architectures and
    x32 syscall encodings kill the process rather than bypass native rules.
    Plain read/write socket protocols and retained file operations remain usable.
    No filter-removal API exists. Never call this in the authority or supervisor.
    This is an additional restriction, not a complete syscall allowlist or a
    custody receipt. Dedicated UID/private proc, closed descriptor handover,
    import/install integrity, namespace/mount policy and exclusion of outside
    descriptor holders remain mandatory. Existing outside holders are unaffected.
    Launch must exclude pre-existing io_uring rings and mappings: blocking these
    entrypoints does not stop an already active kernel polling ring.
    """
    if (
        expected_abi != "linux-x86_64"
        or sys.platform != "linux"
        or os.uname().machine != "x86_64"
        or ctypes.sizeof(ctypes.c_void_p) != 8
    ):
        raise ValueError("writer descriptor filter requires the enrolled native linux-x86_64 ABI")
    # Native constants: linux/seccomp.h, linux/filter.h, asm/unistd_64.h.
    instructions = [
        _Filter(0x20, 0, 0, 4),
        _Filter(0x15, 1, 0, 0xC000003E),
        _Filter(0x06, 0, 0, 0x80000000),
        _Filter(0x20, 0, 0, 0),
        _Filter(0x45, 0, 1, 0x40000000),
        _Filter(0x06, 0, 0, 0x80000000),
    ]
    for number in (46, 47, 299, 307, 425, 426, 427, 438):
        instructions.extend(
            (_Filter(0x15, 0, 1, number), _Filter(0x06, 0, 0, 0x50000 | errno.EPERM))
        )
    instructions.append(_Filter(0x06, 0, 0, 0x7FFF0000))
    array = (_Filter * len(instructions))(*instructions)
    program = _Program(len(instructions), array)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_ulong] * 5
    libc.prctl.restype = ctypes.c_int
    libc.syscall.restype = ctypes.c_long
    ctypes.set_errno(0)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "writer no-new-privileges installation failed")
    ctypes.set_errno(0)
    if libc.syscall(317, 1, 1, ctypes.byref(program)) != 0:
        raise OSError(
            ctypes.get_errno() or errno.EBUSY, "writer descriptor filter synchronization failed"
        )

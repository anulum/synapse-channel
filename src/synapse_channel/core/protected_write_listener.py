# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — root-bound listener and dedicated authority custody
"""Verify actual listener custody before and after the dedicated authority calls listen."""

from __future__ import annotations

import os
import socket
import stat
import struct
import sys
from pathlib import Path
from typing import Literal

DEFAULT_FORBIDDEN_UIDS = frozenset({0, 1000})


def validate_authority_role_policy(
    operator_uid: int,
    *,
    forbidden_uids: frozenset[int] = DEFAULT_FORBIDDEN_UIDS,
) -> int:
    """Require a real non-root authority and a separately enrolled operator UID.

    Parameters
    ----------
    operator_uid:
        Independently selected operator role UID.
    forbidden_uids:
        Explicit deployment exclusions. Root must remain excluded; the default
        also excludes the workstation account UID1000. No observed UID is supplied.

    Returns
    -------
    int
        This process's actual effective UID from the native operating system.

    Raises
    ------
    OSError
        If the native Linux peer-credential backend is unavailable.
    ValueError
        If exclusions are malformed or the actual authority/operator roles overlap.
    """
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        raise OSError("protected authority listeners require Linux SO_PEERCRED")
    if (
        type(forbidden_uids) is not frozenset
        or not 0 < len(forbidden_uids) <= 128
        or 0 not in forbidden_uids
        or any(type(uid) is not int or uid < 0 for uid in forbidden_uids)
    ):
        raise ValueError("invalid protected authority forbidden UID policy")
    authority_uid = os.geteuid()
    if (
        authority_uid in forbidden_uids
        or type(operator_uid) is not int
        or operator_uid < 0
        or operator_uid in forbidden_uids
        or operator_uid == authority_uid
    ):
        raise ValueError("operator and authority require separate dedicated UIDs")
    return authority_uid


def _validate_root_listener_path(path: Path, endpoint: str) -> None:
    """Require the socket and its complete real ancestry to remain root-owned."""
    details = path.lstat()
    if not stat.S_ISSOCK(details.st_mode) or details.st_uid != 0 or details.st_mode & 0o007:
        raise ValueError(f"{endpoint} listener must be root-owned")
    for parent in path.parents:
        details = parent.lstat()
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != 0 or details.st_mode & 0o022:
            raise ValueError(f"{endpoint} listener parent chain must be root-owned")


def validate_protected_authority_listener(
    listener: socket.socket,
    operator_uid: int,
    *,
    forbidden_uids: frozenset[int] = DEFAULT_FORBIDDEN_UIDS,
    endpoint: Literal["operator", "request"] = "operator",
    listening: bool = False,
) -> None:
    """Verify root-bound handoff or the authority's own actual listening socket.

    Parameters
    ----------
    listener:
        Independently retained root-bound Unix stream socket under immutable
        root-owned ancestry. The supervisor binds but does not call listen.
    operator_uid:
        Dedicated operator role distinct from this process and forbidden UIDs.
    forbidden_uids:
        Explicit deployment exclusions passed to the real role-policy check.
    endpoint:
        Operator or protected-request endpoint used in refusal messages.
    listening:
        False checks the bound, not-yet-listening handoff. True additionally
        requires actual SO_PEERCRED to identify this authority as the listen caller.

    Raises
    ------
    ValueError
        If roles, kernel socket state, listen credentials or root path custody differ.
    OSError
        If native credentials or actual filesystem/socket metadata are unavailable.

    Notes
    -----
    Linux client-side SO_PEERCRED identifies the listen caller. Merely inheriting
    and serving a supervisor-listened socket cannot authenticate the authority UID.
    The caller must retain installation, namespace and descriptor custody for the
    entire service lifetime; these observations do not create that isolation.
    """
    authority_uid = validate_authority_role_policy(operator_uid, forbidden_uids=forbidden_uids)
    if (
        listener.family != socket.AF_UNIX
        or listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
    ):
        raise ValueError(f"{endpoint} listener must be a Unix stream socket")
    accepting = listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
    if accepting:
        raw = listener.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        if uid != authority_uid:
            raise ValueError(f"{endpoint} listener was started by another authority")
    if bool(accepting) != listening:
        requirement = "listening under the authority UID" if listening else "bound before listen"
        raise ValueError(f"{endpoint} listener must be {requirement}")
    name = listener.getsockname()
    if type(name) is not str or not name.startswith("/"):
        raise ValueError(f"{endpoint} listener requires an absolute filesystem path")
    _validate_root_listener_path(Path(name), endpoint)

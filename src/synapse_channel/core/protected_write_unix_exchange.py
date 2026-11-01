# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded writer-to-authority Unix exchange
"""Exchange one protected request over an enrolled Unix WebSocket listener."""

from __future__ import annotations

import socket
import stat
import struct
import sys
import threading
from contextlib import suppress
from pathlib import Path

from websockets.sync.client import ClientConnection, unix_connect


def require_native_unix_credentials(
    observed_platform: str, peer_credentials_available: bool
) -> None:
    """Require the observed native backend before any endpoint access.

    Parameters
    ----------
    observed_platform:
        Platform name observed by the host adapter, normally sys.platform.
    peer_credentials_available:
        Whether that host exposes the kernel SO_PEERCRED socket option.

    Raises
    ------
    OSError
        Unless the observed platform is Linux and supports kernel peer credentials.

    Notes
    -----
    This pure domain check neither changes the host nor supplies credential
    evidence. The actual exchange independently reads and checks its kernel peer.
    """
    if observed_platform != "linux" or not peer_credentials_available:
        raise OSError("protected authority exchange requires Linux SO_PEERCRED")


def _validate_endpoint_identity(identity: tuple[int, int], uid: int, gid: int) -> None:
    """Require exact immutable filesystem identity and nonnegative owner IDs."""
    if (
        type(identity) is not tuple
        or len(identity) != 2
        or any(type(part) is not int or part < 0 for part in identity)
        or any(type(owner) is not int or owner < 0 for owner in (uid, gid))
    ):
        raise ValueError("invalid protected authority socket enrollment")


def _validate_endpoint_modes(parent_mode: int, socket_mode: int) -> None:
    """Permit pinned traversal and peer-write masks without writable ancestors."""
    if type(parent_mode) is not int or parent_mode & ~0o755 or parent_mode & 0o700 != 0o700:
        raise ValueError("invalid protected authority parent permission enrollment")
    if type(socket_mode) is not int or socket_mode & ~0o760 or socket_mode & 0o600 != 0o600:
        raise ValueError("invalid protected authority socket permission enrollment")


def _validate_exchange_bounds(max_wire_bytes: int, timeout_seconds: float) -> None:
    """Validate finite positive wire and deadline limits before socket access."""
    if (
        type(max_wire_bytes) is not int
        or max_wire_bytes <= 0
        or type(timeout_seconds) is not float
        or not 0 < timeout_seconds <= 30
    ):
        raise ValueError("invalid protected authority socket enrollment")


def _send_with_deadline(connection: ClientConnection, request: bytes, seconds: float) -> None:
    """Abort this owned socket if its actual synchronous send exceeds the finite deadline."""
    expired = threading.Event()

    def abort() -> None:
        """Interrupt blocked kernel reads/writes without changing receive timeout semantics."""
        expired.set()
        with suppress(OSError):
            connection.socket.shutdown(socket.SHUT_RDWR)

    timer = threading.Timer(seconds, abort)
    timer.name = "protected-authority-send-deadline"
    timer.start()
    try:
        try:
            connection.send(request)
        except Exception as error:
            if expired.is_set():
                raise TimeoutError("protected authority send deadline elapsed") from error
            raise
    finally:
        timer.cancel()
        timer.join()


class ProtectedWriteUnixExchange:
    """Pin one socket path and verify the kernel peer before sending a request.

    The caller enrolls exact parent/socket identities, ownership and peer UID.
    The operator must independently protect the entire parent chain and use
    distinct authority/writer UIDs; checking a path cannot prove either fact.
    The signed status client separately verifies authority response bytes.

    Parameters
    ----------
    path:
        Absolute path to the enrolled Unix WebSocket listener.
    parent_identity, socket_identity:
        Exact device/inode pairs of the private parent directory and socket.
    parent_uid, socket_uid, parent_gid, socket_gid:
        Exact independently enrolled user and group owners of those objects.
    parent_mode, socket_mode:
        Exact permission modes. Parents require owner rwx and forbid group/other
        writes; sockets require owner rw and forbid all other-user access.
        Reviewed group masks or per-UID ACLs permit dedicated peer traversal
        and socket writes; no shared supplementary group is required.
    peer_uid:
        Authority UID required from Linux SO_PEERCRED before request delivery.
    max_wire_bytes:
        Positive bound applied separately to request and response frames.
    timeout_seconds:
        Positive per-stage connection, send, close and receive deadline, at most 30 seconds.

    Raises
    ------
    ValueError
        On malformed enrollment or changed path ownership, mode or identity.
    OSError
        On an unavailable Linux credential backend or filesystem metadata.

    Notes
    -----
    This Linux profile refuses unsupported credential backends explicitly.
    Constructor checks are point-in-time observations; the caller retains
    custody of the namespace and full parent chain for the entire exchange.
    It must independently review actual ACLs and authorize dedicated peers.
    Exact group/mode pins do not establish ACL contents or role isolation.
    """

    def __init__(
        self,
        path: Path,
        *,
        parent_identity: tuple[int, int],
        parent_uid: int,
        parent_gid: int,
        parent_mode: int,
        socket_identity: tuple[int, int],
        socket_uid: int,
        socket_gid: int,
        socket_mode: int,
        peer_uid: int,
        max_wire_bytes: int,
        timeout_seconds: float,
    ) -> None:
        """Enroll exact filesystem and kernel-peer identities for bounded exchanges.

        The absolute path, parent/socket device-inode pairs, ownership and modes
        are independently supplied enrollment facts; construction checks them
        against the actual filesystem. No connection is made here. Raise
        ValueError for invalid scope, bounds or changed path metadata; OSError
        for unavailable metadata or an unsupported native credential backend.
        """
        if not path.is_absolute() or type(peer_uid) is not int or peer_uid < 0:
            raise ValueError("invalid protected authority socket enrollment")
        _validate_endpoint_identity(parent_identity, parent_uid, parent_gid)
        _validate_endpoint_identity(socket_identity, socket_uid, socket_gid)
        _validate_endpoint_modes(parent_mode, socket_mode)
        _validate_exchange_bounds(max_wire_bytes, timeout_seconds)
        require_native_unix_credentials(sys.platform, hasattr(socket, "SO_PEERCRED"))
        self._path = path
        self._parent_identity = parent_identity
        self._parent_uid = parent_uid
        self._parent_gid = parent_gid
        self._parent_mode = parent_mode
        self._socket_identity = socket_identity
        self._socket_uid = socket_uid
        self._socket_gid = socket_gid
        self._socket_mode = socket_mode
        self._peer_uid = peer_uid
        self._max_wire_bytes = max_wire_bytes
        self._timeout = timeout_seconds
        self._check_path()

    def _check_path(self) -> None:
        """Refuse changed parent/socket type, inode, ownership or enrolled mode."""
        parent = self._path.parent.lstat()
        listener = self._path.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or (parent.st_dev, parent.st_ino) != self._parent_identity
            or parent.st_uid != self._parent_uid
            or parent.st_gid != self._parent_gid
            or stat.S_IMODE(parent.st_mode) != self._parent_mode
            or not stat.S_ISSOCK(listener.st_mode)
            or (listener.st_dev, listener.st_ino) != self._socket_identity
            or listener.st_uid != self._socket_uid
            or listener.st_gid != self._socket_gid
            or stat.S_IMODE(listener.st_mode) != self._socket_mode
        ):
            raise ValueError("protected authority socket path identity or ownership changed")

    def __call__(self, request: bytes) -> bytes:
        """Send one bounded request after real path and authority-peer verification.

        Parameters
        ----------
        request:
            One nonempty binary request within the enrolled wire byte bound.

        Returns
        -------
        bytes
            Bounded binary response; the caller still verifies its authority signature.

        Raises
        ------
        ValueError
            If request/response bounds, path identity or actual authority UID differ.
        TimeoutError
            If the send deadline aborts the owned connection or receive times out.
        OSError
            If native socket or retained filesystem metadata is unavailable.

        Notes
        -----
        A timed-out or interrupted request may already be durable. An absent
        response requires journal inspection, never an automatic retry. The
        owned send timer is cancelled and joined before returning or raising.
        """
        if type(request) is not bytes or not 0 < len(request) <= self._max_wire_bytes:
            raise ValueError("invalid protected authority request size")
        self._check_path()
        with unix_connect(
            path=str(self._path),
            uri="ws://localhost/",
            open_timeout=self._timeout,
            close_timeout=self._timeout,
            max_size=self._max_wire_bytes,
            max_queue=1,
            ping_interval=None,
        ) as connection:
            raw = connection.socket.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
            _pid, uid, _gid = struct.unpack("3i", raw)
            if uid != self._peer_uid:
                raise ValueError("protected authority peer UID differs")
            self._check_path()
            _send_with_deadline(connection, request, self._timeout)
            response = connection.recv(timeout=self._timeout)
            if type(response) is not bytes or not 0 < len(response) <= self._max_wire_bytes:
                raise ValueError("invalid protected authority response size")
            self._check_path()
            return response

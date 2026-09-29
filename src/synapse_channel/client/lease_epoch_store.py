# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — keep a lease's fencing epoch across client processes (FENCE-01b)
"""Persist the fencing epoch of each lease an identity holds, across processes.

A lease mutation (task update, release, handoff, checkpoint) names the epoch
of the lease it acts under, so a hub with ``--require-fencing-epoch`` can
refuse a writer holding a superseded lease. The client remembers the epoch of
its own ``claim_granted`` and ``handoff_granted`` frames. The CLI, however,
claims in one process and releases in another (``synapse lock`` then
``synapse release``, ``git-claim`` then the commit hook), so the memory has to
outlive the process. This store keeps it on disk.

Layout: ``<data-home>/synapse/lease-epoch/<identity>/<hub id>/<task id>``, each
path component URL-quoted with no safe characters, so a name such as
``project/seat`` stays one flat component, and every dot is escaped too, so
no component can be ``.`` or ``..`` and climb out of its directory. An empty
identity, hub id or task id is never stored. Every file holds one decimal epoch
and is replaced atomically, so concurrent processes of one identity each
update their own task file and never lose each other's writes. Directories are
created ``0o700`` and files ``0o600``.

The store is a convenience for the identity's own writes, not a trust input.
The hub still decides: a stale epoch read from here is refused exactly like a
stale epoch held in memory. A missing, unreadable or malformed file reads as
no epoch. Write failures are ignored, so a read-only home costs only the
cross-process memory and never a crash.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

LEASE_EPOCH_DIR_MODE = 0o700
LEASE_EPOCH_FILE_MODE = 0o600
_MAX_EPOCH_TEXT = 32
_EPOCH_TEXT = re.compile(r"(0|[1-9][0-9]*)")


def _component(value: str) -> str | None:
    """Return ``value`` as one safe path component, or ``None`` when empty."""
    if not value:
        return None
    return quote(value, safe="").replace(".", "%2E")


def default_lease_epoch_root(*, base: Path | None = None) -> Path:
    """Return the root directory of the lease-epoch store.

    Parameters
    ----------
    base : pathlib.Path or None, optional
        Data-home override. ``None`` reads ``$XDG_DATA_HOME`` and falls back to
        ``~/.local/share``, like the machine identity and the payload replay
        ledger.

    Returns
    -------
    pathlib.Path
        ``<data-home>/synapse/lease-epoch``.
    """
    if base is None:
        raw = os.environ.get("XDG_DATA_HOME", "").strip()
        base = Path(raw) if raw else Path.home() / ".local" / "share"
    return base / "synapse" / "lease-epoch"


class LeaseEpochStore:
    """Per-identity, per-hub, per-task lease epochs on disk.

    Parameters
    ----------
    identity : str
        The agent name whose leases these are.
    root : pathlib.Path or None, optional
        Store root; ``None`` uses :func:`default_lease_epoch_root`.
    """

    def __init__(self, identity: str, *, root: Path | None = None) -> None:
        base = root if root is not None else default_lease_epoch_root()
        name = _component(identity)
        self.directory: Path | None = base / name if name is not None else None

    def _path(self, hub_id: str, task_id: str) -> Path | None:
        hub, task = _component(hub_id), _component(task_id)
        if self.directory is None or hub is None or task is None:
            return None
        return self.directory / hub / task

    def load(self, hub_id: str, task_id: str) -> int | None:
        """Return the stored epoch for ``task_id`` on ``hub_id``, or ``None``.

        Parameters
        ----------
        hub_id, task_id : str
            The hub the lease lives on and the task it covers.

        Returns
        -------
        int or None
            The epoch, or ``None`` when nothing well-formed is stored.
        """
        path = self._path(hub_id, task_id)
        if path is None:
            return None
        try:
            with path.open("r", encoding="ascii") as handle:
                text = handle.read(_MAX_EPOCH_TEXT + 1).strip()
        except (OSError, UnicodeDecodeError):
            return None
        if len(text) > _MAX_EPOCH_TEXT or _EPOCH_TEXT.fullmatch(text) is None:
            return None
        return int(text)

    def save(self, hub_id: str, task_id: str, epoch: int) -> None:
        """Store ``epoch`` for ``task_id`` on ``hub_id``, atomically and privately.

        Parameters
        ----------
        hub_id, task_id : str
            The hub the lease lives on and the task it covers.
        epoch : int
            The non-negative epoch from the grant frame. A negative value is
            not stored.
        """
        target = self._path(hub_id, task_id)
        if target is None or epoch < 0:
            return
        try:
            for directory in (target.parent.parent.parent, target.parent.parent, target.parent):
                directory.mkdir(mode=LEASE_EPOCH_DIR_MODE, parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".", suffix=".tmp")
        except OSError:
            return
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(str(epoch))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, LEASE_EPOCH_FILE_MODE)
            os.replace(tmp, target)
        except OSError:
            tmp.unlink(missing_ok=True)

    def forget(self, hub_id: str, task_id: str) -> None:
        """Remove the stored epoch for ``task_id`` on ``hub_id``, if any.

        Parameters
        ----------
        hub_id, task_id : str
            The hub the lease lived on and the task it covered.
        """
        path = self._path(hub_id, task_id)
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return

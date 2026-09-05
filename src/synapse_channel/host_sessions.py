# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared host-session observations
"""Cache immutable metadata observations for terminal and HTTP consumers."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from synapse_channel.host_sessions_proc import (
    PROVIDERS,
    KernelClock,
    MetadataStatus,
    discover_processes,
    kernel_clock,
    observe_process,
    process_metadata,
)
from synapse_channel.host_sessions_tmux import PaneMetadata, observe_tmux
from synapse_channel.waiter_identity import is_waiter, waiter_owner

CoordinationReader = Callable[[], tuple[tuple[str, ...], tuple[tuple[str, str], ...]]]

StartTimeStatus = Literal["observed", "unavailable"]

MAXIMUM_BUDGET_SECONDS = 5.0
"""Ceiling for the per-observation row-assembly budget, in seconds."""


@dataclass(frozen=True)
class HostSession:
    """Process and pane evidence; assertions never authorise process actions.

    Attributes
    ----------
    reference : str
        Opaque hash of boot identity, PID and process start ticks.
    pid, parent_pid, start_ticks : int
        Kernel process identifiers and boot-relative start ticks. A PID alone
        is not a process lifetime or a safe target for an action.
    command_name, state : str
        Kernel comm and state, not verified executable identity or agent activity.
    provider : str or None
        Known provider name matching comm; a heuristic candidate only.
    identity, project, session, pane : str or None
        tmux session assertions and identifiers joined through observed ancestry.
    attached : bool or None
        Whether tmux reports an attached client, not desktop window visibility.
    identity_source : str
        tmux-session-assertion, conflicting-tmux-assertion, or unknown.
    cwd, context_id : str or None
        Opt-in directory pathname and unique open rollout pathname UUID.
        Only observed values are populated; no transcript body is read.
    cwd_status, context_status : str
        Per-field evidence: not_requested, observed, unavailable, denied,
        conflicting, partial or unsupported. Incomplete FD enumeration never
        proves uniqueness; null data is explained by its corresponding status.
    paths_requested, context_requested : bool
        Disclosure requested by the caller, not proof the metadata was readable.
    presence : bool or None
        Whether the asserted seat appeared in the coordination roster. Null
        means no usable assertion or no coordination observation.
    waiters, claims : tuple of str
        Exact waiter names and active task IDs associated with the asserted seat.
        Empty with unavailable coordination does not prove their absence.
    duplicate_identity : bool
        Multiple provider candidates asserted the same seat in this observation.
    started_at : float or None
        Estimated Unix seconds when the kernel started the process, derived from
        the boot time in ``/proc/stat`` and ``start_ticks``. Resolution is about
        one second. Runtime age is ``observed_at - started_at``; it is distinct
        from observation age and proves neither activity nor responsiveness.
    started_at_status : str
        observed when the boot reference was readable and the derived start is
        not after the observation, otherwise unavailable with a null value.
    """

    reference: str
    pid: int
    parent_pid: int
    start_ticks: int
    command_name: str
    state: str
    provider: str | None
    identity: str | None
    project: str | None
    session: str | None
    pane: str | None
    attached: bool | None
    identity_source: str
    cwd: str | None
    context_id: str | None
    paths_requested: bool
    context_requested: bool
    presence: bool | None = None
    waiters: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    duplicate_identity: bool = False
    cwd_status: MetadataStatus = "not_requested"
    context_status: MetadataStatus = "not_requested"
    started_at: float | None = None
    started_at_status: StartTimeStatus = "unavailable"


@dataclass(frozen=True)
class HostObservation:
    """One boot-scoped observation, with explicit source completeness.

    Attributes
    ----------
    version : int
        Wire schema version, currently 1.
    observation_id, observer_instance_id, host_ref : str
        Opaque observation, monitor-instance and boot references. Independently
        created monitors do not promise identical observation IDs or timestamps.
    observed_at, coordination_observed_at : float
        Unix seconds for collection completion and coordination receipt. The
        coordination time is null when that source was unavailable.
    valid_for_seconds : float
        Maximum client display lifetime in seconds after observed_at.
    process_status, tmux_status, coordination_status : str
        Source completeness: complete, partial or unavailable. Complete is not
        an atomic machine snapshot, an activity diagnosis or an authority grant.
    rows : tuple of HostSession
        At most 256 immutable rows, ordered by PID.
    """

    version: int
    observation_id: str
    observer_instance_id: str
    host_ref: str
    observed_at: float
    valid_for_seconds: float
    process_status: str
    tmux_status: str
    coordination_status: str
    coordination_observed_at: float | None
    rows: tuple[HostSession, ...]

    def to_json(self) -> bytes:
        """Serialise the immutable observation without executable arguments.

        Returns
        -------
        bytes
            ASCII-escaped UTF-8 JSON including explicit null metadata.

        Raises
        ------
        ValueError
            If manually constructed fields contain non-finite numbers.
        """
        return json.dumps(asdict(self), ensure_ascii=True, allow_nan=False).encode("utf-8")


class HostSessionMonitor:
    """Share one bounded observation between concurrent viewers.

    Parameters
    ----------
    pids : tuple of int, optional
        Explicit diagnostic process scope; empty discovers provider candidates.
    tmux_socket : str or None, optional
        Local socket selection, also useful for isolated real fixtures.
    context_root : pathlib.Path or None, optional
        Allowed context pathname root for a non-default installation. Selecting
        a root does not grant context disclosure or read any transcript body.
    coordination : callable or None, optional
        Bounded reader returning online seat names and (owner, task ID) pairs.
        The dashboard supplies its shared identity gate and network deadlines.
        Standalone monitors have no coordination source. The callback must not
        block indefinitely; this synchronous monitor cannot cancel caller code.
    budget_seconds : float, optional
        Monotonic budget for joining, validating and describing rows after the
        process and tmux scans, in seconds. Exhausting it marks the process
        source partial and withholds rows not yet assembled. Defaults to 0.25;
        at most 5.0.

    Raises
    ------
    ValueError
        If explicit scope exceeds 256 PIDs or contains a non-positive integer,
        or the budget is not a finite number within ``[0, 5.0]``.
    """

    def __init__(
        self,
        *,
        pids: tuple[int, ...] = (),
        tmux_socket: str | None = None,
        context_root: Path | None = None,
        coordination: CoordinationReader | None = None,
        budget_seconds: float = 0.25,
    ) -> None:
        if len(pids) > 256 or any(type(pid) is not int or pid <= 0 for pid in pids):
            raise ValueError("monitor accepts at most 256 positive PIDs")
        if (
            type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds)
            or not 0 <= budget_seconds <= MAXIMUM_BUDGET_SECONDS
        ):
            raise ValueError("budget must be a finite number of seconds within [0, 5.0]")
        self.pids = pids
        self.tmux_socket = tmux_socket
        self.context_root = context_root.resolve() if context_root is not None else None
        self.budget_seconds = float(budget_seconds)
        self.instance = uuid.uuid4().hex
        self.coordination = coordination
        self._lock = threading.Lock()
        self._cached: dict[tuple[bool, bool], tuple[float, HostObservation]] = {}

    def snapshot(self, *, paths: bool = False, context: bool = False) -> HostObservation:
        """Return an observation cached for one second per disclosure profile.

        Parameters
        ----------
        paths, context : bool, optional
            Explicit consent to directory or context pathname metadata. HTTP
            callers must authorise these flags before calling this method.

        Returns
        -------
        HostObservation
            Shared immutable observation for this monitor and disclosure pair.
            Source failures remain explicit; stale cache entries are not served
            as recovery evidence after a failed collection.

        Raises
        ------
        TimeoutError
            Another collection holds the lock for more than 50 milliseconds.
        TypeError
            A disclosure flag is not a boolean.
        """
        if type(paths) is not bool or type(context) is not bool:
            raise TypeError("disclosure flags must be booleans")
        if not self._lock.acquire(timeout=0.05):
            raise TimeoutError("host observation already in progress")
        try:
            key = (paths, context)
            cached = self._cached.get(key)
            if cached is not None and time.monotonic() < cached[0]:
                return cached[1]
            observation = self._collect(paths=paths, context=context)
            self._cached[key] = (time.monotonic() + 1.0, observation)
            return observation
        finally:
            self._lock.release()

    def _collect(self, *, paths: bool, context: bool) -> HostObservation:
        online: tuple[str, ...] = ()
        claims: tuple[tuple[str, str], ...] = ()
        coordination_status = "unavailable"
        coordination_observed_at = None
        if self.coordination is not None:
            try:
                online, claims = self.coordination()
                coordination_status = "complete"
                coordination_observed_at = time.time()
            except (OSError, RuntimeError, TimeoutError):
                pass
        processes, process_status = discover_processes(pids=self.pids)
        panes, tmux_status = observe_tmux(self.tmux_socket)
        roots: dict[int, PaneMetadata] = {}
        conflicting_roots: set[int] = set()
        for pane_metadata in panes:
            if pane_metadata.pid in roots and roots[pane_metadata.pid] != pane_metadata:
                conflicting_roots.add(pane_metadata.pid)
                tmux_status = "partial"
            roots[pane_metadata.pid] = pane_metadata
        try:
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            boot = self.instance
            process_status = "unavailable"
        host_ref = hashlib.sha256(boot.encode()).hexdigest()[:24]
        clock: KernelClock | None
        try:
            clock = kernel_clock()
        except (OSError, ValueError):
            clock = None
        rows: list[HostSession] = []
        deadline = time.monotonic() + self.budget_seconds
        for process in processes.values():
            if time.monotonic() >= deadline:
                process_status = "partial"
                break
            pane: PaneMetadata | None = None
            pane_conflict = False
            ancestor = process
            ancestry = []
            for _ in range(64):
                ancestry.append(ancestor)
                if ancestor.pid in conflicting_roots:
                    pane_conflict = True
                    break
                if ancestor.pid in roots:
                    pane = roots[ancestor.pid]
                    break
                parent = processes.get(ancestor.parent_pid)
                if parent is None or parent.start_ticks > ancestor.start_ticks:
                    break
                ancestor = parent
            provider = process.command_name if process.command_name in PROVIDERS else None
            if not self.pids and provider is None and pane is None and not pane_conflict:
                continue
            if len(rows) >= 256:
                process_status = "partial"
                break
            try:
                metadata = process_metadata(
                    process.pid,
                    paths=paths,
                    context=context,
                    context_root=self.context_root,
                    expected_start_ticks=process.start_ticks,
                )
                for observed in ancestry if pane is not None else [process]:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("ancestor validation budget exhausted")
                    current = observe_process(observed.pid)
                    if (
                        current.start_ticks != observed.start_ticks
                        or current.parent_pid != observed.parent_pid
                        or current.command_name != observed.command_name
                    ):
                        raise ProcessLookupError("process ancestry changed")
            except (OSError, ValueError, IndexError):
                process_status = "partial"
                continue
            identity = pane.identity if pane else None
            project = pane.project if pane else None
            identity_source = "tmux-session-assertion" if identity else "unknown"
            if pane_conflict:
                identity_source = "conflicting-tmux-assertion"
            if identity and (not project or identity.split("/", 1)[0] != project):
                identity = None
                identity_source = "conflicting-tmux-assertion"
            reference = hashlib.sha256(
                f"{boot}:{process.pid}:{process.start_ticks}".encode()
            ).hexdigest()[:32]
            rows.append(
                HostSession(
                    reference,
                    process.pid,
                    process.parent_pid,
                    process.start_ticks,
                    process.command_name,
                    process.state,
                    provider,
                    identity,
                    project,
                    pane.session if pane else None,
                    pane.pane if pane else None,
                    pane.attached if pane else None,
                    identity_source,
                    metadata.cwd,
                    metadata.context_id,
                    paths,
                    context,
                    cwd_status=metadata.cwd_status,
                    context_status=metadata.context_status,
                )
            )
        identities = [row.identity for row in rows if row.provider and row.identity]
        observed_at = time.time()
        described: list[HostSession] = []
        for row in rows:
            started_at = self._started_at(clock, row.start_ticks, observed_at)
            described.append(
                replace(
                    row,
                    started_at=started_at,
                    started_at_status="unavailable" if started_at is None else "observed",
                    duplicate_identity=bool(
                        row.provider and row.identity and identities.count(row.identity) > 1
                    ),
                    presence=(
                        row.identity in online
                        if row.identity and coordination_status == "complete"
                        else None
                    ),
                    waiters=tuple(
                        name
                        for name in online
                        if row.identity and is_waiter(name) and waiter_owner(name) == row.identity
                    ),
                    claims=tuple(
                        task for owner, task in claims if row.identity and owner == row.identity
                    ),
                )
            )
        rows = described
        return HostObservation(
            1,
            uuid.uuid4().hex,
            self.instance,
            host_ref,
            observed_at,
            3.0,
            process_status,
            tmux_status,
            coordination_status,
            coordination_observed_at,
            tuple(sorted(rows, key=lambda row: row.pid)),
        )

    @staticmethod
    def _started_at(
        clock: KernelClock | None, start_ticks: int, observed_at: float
    ) -> float | None:
        if clock is None:
            return None
        started_at = clock.started_at(start_ticks)
        return started_at if started_at <= observed_at else None

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — JetBrains ACP lifecycle cardinality guard
"""Enforce one JetBrains chat, process, and ACP trace for an editor turn."""

from __future__ import annotations

import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

_MAX_POST_BASELINE_LOG_BYTES = 4_194_304
LogIdentity = tuple[int, int]
Event = TypeVar("Event")


@dataclass(frozen=True, slots=True)
class JetBrainsLifecycleObservation:
    """Observed post-baseline identifiers for one JetBrains ACP selection.

    Parameters
    ----------
    chat_ids:
        Chat identifiers emitted by the pinned lifecycle-manager registry.
    process_ids:
        Operating-system process identifiers emitted for the selected agent.
    trace_segments:
        Private ACP trace files created by the evidence proxy.
    """

    chat_ids: tuple[str, ...]
    process_ids: tuple[int, ...]
    trace_segments: tuple[Path, ...]


@dataclass(slots=True)
class JetBrainsLifecycleGuard:
    """Continuously enforce one selected JetBrains ACP lifecycle.

    The guard records byte offsets before the selector is opened. Each later
    observation reads only evidence appended after those offsets and rejects
    log rotation, unsafe files, divergent IDEA/ACP logs, or a second backend
    lifecycle.

    Parameters
    ----------
    idea_log:
        Pinned IDEA application log.
    acp_log:
        Pinned AI Assistant ACP log.
    trace:
        First trace path assigned to the ACP evidence proxy.
    idea_offset:
        IDEA log byte offset captured before agent selection.
    acp_offset:
        ACP log byte offset captured before agent selection.
    agent_id:
        Exact registered ACP agent identifier.
    agent_name:
        Exact public agent name used in process-start evidence.
    idea_identity, acp_identity:
        Device and inode captured for each existing baseline log. An absent
        log is bound to its first safe post-baseline creation.
    """

    idea_log: Path
    acp_log: Path
    trace: Path
    idea_offset: int
    acp_offset: int
    agent_id: str
    agent_name: str
    idea_identity: LogIdentity | None = None
    acp_identity: LogIdentity | None = None

    @classmethod
    def capture(
        cls,
        log_root: Path,
        trace: Path,
        *,
        agent_id: str,
        agent_name: str,
    ) -> JetBrainsLifecycleGuard:
        """Capture a clean lifecycle baseline before opening the selector.

        Parameters
        ----------
        log_root:
            Isolated IDEA log root for this editor process.
        trace:
            First trace path assigned to the ACP evidence proxy.
        agent_id:
            Exact registered ACP agent identifier.
        agent_name:
            Exact public agent name used in process-start evidence.

        Returns
        -------
        JetBrainsLifecycleGuard
            Guard anchored to the current safe log offsets.

        Raises
        ------
        RuntimeError
            If identifiers are empty, logs are unsafe, or trace evidence
            already exists before selection.
        """
        if not agent_id.strip() or not agent_name.strip():
            raise RuntimeError("JetBrains lifecycle identities must be non-empty")
        if _trace_segments(trace):
            raise RuntimeError("JetBrains ACP trace exists before agent selection")
        idea_log = log_root / "idea.log"
        acp_log = log_root / "acp" / "acp.log"
        idea_offset, idea_identity = _safe_baseline(idea_log)
        acp_offset, acp_identity = _safe_baseline(acp_log)
        return cls(
            idea_log=idea_log,
            acp_log=acp_log,
            trace=trace,
            idea_offset=idea_offset,
            acp_offset=acp_offset,
            agent_id=agent_id,
            agent_name=agent_name,
            idea_identity=idea_identity,
            acp_identity=acp_identity,
        )

    def observe(self) -> JetBrainsLifecycleObservation:
        """Read one coherent post-baseline lifecycle observation.

        Returns
        -------
        JetBrainsLifecycleObservation
            Chat, process, and trace evidence observed so far.

        Raises
        ------
        RuntimeError
            If the evidence files are unsafe, truncated, oversized, malformed,
            or disagree across IDEA's mirrored logs.
        """
        idea_contents, idea_identity = _read_since(
            self.idea_log,
            self.idea_offset,
            self.idea_identity,
        )
        acp_contents, acp_identity = _read_since(
            self.acp_log,
            self.acp_offset,
            self.acp_identity,
        )
        self.idea_identity = idea_identity
        self.acp_identity = acp_identity
        chat_pattern = re.compile(
            r"Creating AcpSessionLifecycleManager for agent '(?P<agent>[^']+)' "
            r"in chat (?P<value>\S+)"
        )
        process_pattern = re.compile(
            r"\[(?P<agent>[^]]+)\] Process started with PID: "
            r"LocalPid\(value=(?P<value>[^)]+)\)"
        )
        chat_events = _coherent_events(
            "chat",
            _matched_events(idea_contents, chat_pattern),
            _matched_events(acp_contents, chat_pattern),
        )
        process_events = _coherent_events(
            "process",
            _matched_events(idea_contents, process_pattern),
            _matched_events(acp_contents, process_pattern),
        )
        unexpected_chat_agents = tuple(
            agent for agent, _value in chat_events if agent != self.agent_id
        )
        unexpected_process_agents = tuple(
            agent for agent, _value in process_events if agent != self.agent_name
        )
        if unexpected_chat_agents or unexpected_process_agents:
            raise RuntimeError(
                "JetBrains started an unexpected post-selection ACP lifecycle: "
                f"chat_agents={unexpected_chat_agents!r}, "
                f"process_agents={unexpected_process_agents!r}"
            )
        return JetBrainsLifecycleObservation(
            chat_ids=tuple(_canonical_chat_id(value) for _agent, value in chat_events),
            process_ids=tuple(_process_id(value) for _agent, value in process_events),
            trace_segments=_trace_segments(self.trace),
        )

    def idea_contents(self) -> str:
        """Return safe IDEA log contents appended after the captured baseline."""
        contents, identity = _read_since(
            self.idea_log,
            self.idea_offset,
            self.idea_identity,
        )
        self.idea_identity = identity
        return contents

    def assert_at_most_one(self) -> JetBrainsLifecycleObservation:
        """Reject a second lifecycle while allowing the first to start.

        Returns
        -------
        JetBrainsLifecycleObservation
            Current evidence when every cardinality is zero or one.

        Raises
        ------
        RuntimeError
            If a second chat, process, or trace exists, or trace numbering
            starts anywhere except the configured base path.
        """
        observation = self.observe()
        counts = (
            len(observation.chat_ids),
            len(observation.process_ids),
            len(observation.trace_segments),
        )
        if any(count > 1 for count in counts):
            raise RuntimeError(
                "JetBrains created multiple ACP lifecycles: "
                f"chats={counts[0]}, processes={counts[1]}, traces={counts[2]}"
            )
        if observation.trace_segments and observation.trace_segments != (self.trace,):
            raise RuntimeError("JetBrains ACP trace sequence did not start at the base path")
        return observation

    def require_none(self) -> JetBrainsLifecycleObservation:
        """Require that selector interaction has not created a lifecycle.

        Returns
        -------
        JetBrainsLifecycleObservation
            Empty lifecycle evidence before explicit agent confirmation.

        Raises
        ------
        RuntimeError
            If any chat, process, or trace has already started.
        """
        observation = self.assert_at_most_one()
        counts = (
            len(observation.chat_ids),
            len(observation.process_ids),
            len(observation.trace_segments),
        )
        if counts != (0, 0, 0):
            raise RuntimeError(
                "JetBrains created an ACP lifecycle before agent confirmation: "
                f"chats={counts[0]}, processes={counts[1]}, traces={counts[2]}"
            )
        return observation

    def require_exactly_one(self) -> JetBrainsLifecycleObservation:
        """Require one chat, one process, and one base trace.

        Returns
        -------
        JetBrainsLifecycleObservation
            Complete exact-cardinality evidence for the selected lifecycle.

        Raises
        ------
        RuntimeError
            If any lifecycle component is absent or duplicated.
        """
        observation = self.assert_at_most_one()
        counts = (
            len(observation.chat_ids),
            len(observation.process_ids),
            len(observation.trace_segments),
        )
        if counts != (1, 1, 1):
            raise RuntimeError(
                "JetBrains did not create exactly one ACP lifecycle: "
                f"chats={counts[0]}, processes={counts[1]}, traces={counts[2]}"
            )
        return observation


def _safe_baseline(path: Path) -> tuple[int, LogIdentity | None]:
    """Return an owned regular file's size and identity, or an absent baseline."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 0, None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise RuntimeError(f"unsafe JetBrains lifecycle log: {path}")
    return metadata.st_size, (metadata.st_dev, metadata.st_ino)


def _read_since(
    path: Path,
    offset: int,
    expected_identity: LogIdentity | None,
) -> tuple[str, LogIdentity | None]:
    """Read a bounded log while preserving its captured device and inode."""
    if offset < 0:
        raise RuntimeError("JetBrains lifecycle log offset cannot be negative")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if offset or expected_identity is not None:
            raise RuntimeError(f"JetBrains lifecycle log disappeared: {path}") from None
        return "", None
    except OSError as exc:
        raise RuntimeError(f"JetBrains lifecycle log could not be opened: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError(f"unsafe JetBrains lifecycle log: {path}")
        identity = (metadata.st_dev, metadata.st_ino)
        if expected_identity is not None and identity != expected_identity:
            raise RuntimeError(f"JetBrains lifecycle log was replaced: {path}")
        if metadata.st_size < offset:
            raise RuntimeError(f"JetBrains lifecycle log was truncated: {path}")
        length = metadata.st_size - offset
        if length > _MAX_POST_BASELINE_LOG_BYTES:
            raise RuntimeError(f"JetBrains lifecycle log exceeded four MiB: {path}")
        os.lseek(descriptor, offset, os.SEEK_SET)
        payload = bytearray()
        while len(payload) < length:
            chunk = os.read(descriptor, length - len(payload))
            if not chunk:
                raise RuntimeError(f"JetBrains lifecycle log changed while reading: {path}")
            payload.extend(chunk)
        try:
            current = path.lstat()
        except FileNotFoundError:
            raise RuntimeError(f"JetBrains lifecycle log changed while reading: {path}") from None
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.getuid()
            or (current.st_dev, current.st_ino) != identity
        ):
            raise RuntimeError(f"JetBrains lifecycle log changed while reading: {path}")
    finally:
        os.close(descriptor)
    try:
        return payload.decode("utf-8"), identity
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"JetBrains lifecycle log is not valid UTF-8: {path}") from exc


def _matched_events(
    contents: str,
    pattern: re.Pattern[str],
) -> tuple[tuple[str, str], ...]:
    """Return ordered agent/value pairs from one lifecycle event pattern."""
    return tuple(
        (match.group("agent"), match.group("value")) for match in pattern.finditer(contents)
    )


def _coherent_events(
    label: str,
    idea_events: tuple[Event, ...],
    acp_events: tuple[Event, ...],
) -> tuple[Event, ...]:
    """Reconcile mirrored IDEA and ACP logs without double-counting events."""
    if len(idea_events) >= len(acp_events) and idea_events[: len(acp_events)] == acp_events:
        return idea_events
    if acp_events[: len(idea_events)] == idea_events:
        return acp_events
    raise RuntimeError(f"JetBrains IDEA and ACP {label} lifecycle logs disagree")


def _canonical_chat_id(value: str) -> str:
    """Validate and return one lowercase canonical UUID chat identifier."""
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise RuntimeError(f"JetBrains emitted an invalid ACP chat id: {value!r}") from exc
    canonical = str(parsed)
    if canonical != value:
        raise RuntimeError(f"JetBrains emitted a non-canonical ACP chat id: {value!r}")
    return canonical


def _process_id(value: str) -> int:
    """Validate and return one positive decimal process identifier."""
    if not value.isdecimal():
        raise RuntimeError(f"JetBrains emitted an invalid ACP process id: {value!r}")
    process_id = int(value)
    if process_id <= 0:
        raise RuntimeError(f"JetBrains emitted an invalid ACP process id: {value!r}")
    return process_id


def _trace_segments(trace: Path) -> tuple[Path, ...]:
    """Return naturally ordered regular trace segments, including unsafe paths."""
    candidates: list[tuple[int, Path]] = []
    if trace.exists() or trace.is_symlink():
        candidates.append((0, trace))
    for candidate in trace.parent.glob(f"{trace.name}.*"):
        suffix = candidate.name.removeprefix(f"{trace.name}.")
        if suffix.isdecimal():
            candidates.append((int(suffix) + 1, candidate))
    ordered = tuple(path for _index, path in sorted(candidates))
    for path in ordered:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError(f"unsafe JetBrains ACP trace segment: {path}")
    return ordered

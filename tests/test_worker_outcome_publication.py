# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — worker report publication: raced writes refused, failed writes cleaned up
"""A raced report write is never lost; a write the kernel refuses leaves no residue."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _platform_caps import requires_proc
from synapse_channel.execution_outcome import OutcomeState
from synapse_channel.worker_outcome import WorkerObservation

pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX keeps an open descriptor across a rename over its path; Windows refuses it",
)

_REFUSAL = "^worker outcome record changed; inquire before continuing$"


def _overwrite(descriptor: int, data: bytes) -> None:
    os.ftruncate(descriptor, 0)
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.write(descriptor, data)


def test_a_write_into_the_replaced_inode_is_restored_and_refused(tmp_path: Path) -> None:
    """The provider opened the report before an update; its later write still counts."""
    report = tmp_path / "private" / "outcome.json"
    observation = WorkerObservation(report)
    provider = os.open(report, os.O_WRONLY)
    try:
        observation.observe(OutcomeState.STARTED, "provider_started")
        _overwrite(provider, b"UNTRUSTED_REPLACEMENT")
    finally:
        os.close(provider)
    with pytest.raises(ValueError, match=_REFUSAL):
        observation.finish(0)
    assert report.read_bytes() == b"UNTRUSTED_REPLACEMENT"
    assert [path.name for path in report.parent.iterdir()] == ["outcome.json"]


def test_a_change_at_the_path_wins_over_a_raced_write(tmp_path: Path) -> None:
    """When the path itself changed too, that visible change is never overwritten."""
    report = tmp_path / "private" / "outcome.json"
    observation = WorkerObservation(report)
    provider = os.open(report, os.O_WRONLY)
    try:
        observation.observe(OutcomeState.STARTED, "provider_started")
        _overwrite(provider, b"WRITTEN_INTO_THE_OLD_INODE")
    finally:
        os.close(provider)
    report.write_bytes(b"WRITTEN_AT_THE_PATH")
    with pytest.raises(ValueError, match=_REFUSAL):
        observation.finish(0)
    assert report.read_bytes() == b"WRITTEN_AT_THE_PATH"


def _descriptors() -> set[str] | None:
    return set(os.listdir("/proc/self/fd")) if Path("/proc/self/fd").is_dir() else None


def test_a_write_delayed_past_two_updates_is_still_refused(tmp_path: Path) -> None:
    """Every replaced inode stays watched, so a late write into the first is not lost."""
    report = tmp_path / "private" / "outcome.json"
    before = _descriptors()
    observation = WorkerObservation(report)
    provider = os.open(report, os.O_WRONLY)
    try:
        observation.observe(OutcomeState.STARTED, "provider_launch_invoked")
        observation.observe(OutcomeState.STARTED, "provider_started")
        _overwrite(provider, b"LATE_WRITE_INTO_THE_FIRST_INODE")
    finally:
        os.close(provider)
    with pytest.raises(ValueError, match=_REFUSAL):
        observation.observe(OutcomeState.UNKNOWN, "provider_wait_interrupted")
    assert report.read_bytes() == b"LATE_WRITE_INTO_THE_FIRST_INODE"
    if before is not None:
        assert _descriptors() == before


def test_untouched_updates_publish_and_release_every_descriptor(tmp_path: Path) -> None:
    """Without interference every update lands, and finishing leaves no descriptor open."""
    report = tmp_path / "private" / "outcome.json"
    before = _descriptors()
    observation = WorkerObservation(report)
    observation.observe(OutcomeState.STARTED, "provider_launch_invoked")
    observation.observe(OutcomeState.STARTED, "provider_started")
    result = observation.finish(0)
    assert int(result) == 0
    assert b'"provider_process_exited"' in report.read_bytes()
    if before is not None:
        assert _descriptors() == before


_FILE_SIZE_LIMIT = r"""
import errno, os, resource, signal, sys
from pathlib import Path
from synapse_channel.execution_outcome import OutcomeState
from synapse_channel.worker_outcome import WorkerObservation

signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
report, phase = Path(sys.argv[1]), sys.argv[2]
soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
observation = WorkerObservation(report) if phase == "update" else None
first = report.read_bytes() if observation is not None else b""
descriptors = set(os.listdir("/proc/self/fd"))
resource.setrlimit(resource.RLIMIT_FSIZE, (16, hard))
try:
    if observation is None:
        WorkerObservation(report)
    else:
        observation.observe(OutcomeState.STARTED, "provider_started")
except OSError as exc:
    refused = exc.errno == errno.EFBIG
else:
    refused = False
finally:
    resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
leaked = set(os.listdir("/proc/self/fd")) - descriptors
print(refused, sorted(leaked), report.read_bytes() == first if first else "", sep="|")
"""


@requires_proc
@pytest.mark.parametrize("phase", ["create", "update"])
def test_a_refused_report_write_leaks_no_descriptor_or_temporary(
    tmp_path: Path, phase: str
) -> None:
    """EFBIG from the kernel propagates; descriptors close and an update leaves the old bytes."""
    report = tmp_path / "private" / "outcome.json"
    completed = subprocess.run(
        [sys.executable, "-c", _FILE_SIZE_LIMIT, str(report), phase],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    refused, leaked, unchanged = completed.stdout.strip().split("|")
    assert (refused, leaked) == ("True", "[]")
    assert unchanged == ("True" if phase == "update" else "")
    assert [path.name for path in report.parent.iterdir()] == ["outcome.json"]

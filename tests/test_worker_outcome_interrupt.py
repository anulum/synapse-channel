# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an interrupted outcome publish still owns the bytes it replaced
"""A publish interrupted after its own replace must not refuse that record."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from synapse_channel.durable_dir import sync_directory
from synapse_channel.execution_outcome import OutcomeState
from synapse_channel.worker_outcome import WorkerObservation

_REFUSAL = "^worker outcome record changed; inquire before continuing$"


def _descriptors() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


def _interrupt_at(monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    """Raise ``KeyboardInterrupt`` once, after the next replace has landed."""
    fired = False
    if where == "replace":
        real_replace = os.replace

        def replace_then_interrupt(source: str | Path, destination: str | Path) -> None:
            nonlocal fired
            real_replace(source, destination)
            if not fired:
                fired = True
                raise KeyboardInterrupt

        monkeypatch.setattr(os, "replace", replace_then_interrupt)
        return
    real_sync = sync_directory

    def sync_then_interrupt(directory: Path) -> None:
        nonlocal fired
        real_sync(directory)
        if not fired:
            fired = True
            raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.worker_outcome.sync_directory", sync_then_interrupt)


@pytest.mark.parametrize("where", ["replace", "sync"])
def test_an_interrupt_after_the_replace_keeps_the_publishers_own_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """Ctrl-C after the replace, or during the directory sync, is not a foreign write."""
    report = tmp_path / "private" / "outcome.json"
    observation = WorkerObservation(report)
    _interrupt_at(monkeypatch, where)
    with pytest.raises(KeyboardInterrupt):
        observation.observe(OutcomeState.STARTED, "provider_started")
    landed = json.loads(report.read_bytes())
    assert landed["observation"] == "provider_started"
    observation.observe(
        OutcomeState.UNKNOWN,
        "provider_wait_interrupted",
        stop_requested=True,
        stop_confirmed=True,
    )
    result = json.loads(report.read_bytes())
    assert result["state"] == result["external_effect"] == "unknown"
    assert result["observation"] == "provider_wait_interrupted"
    assert result["stop_requested"] is True
    assert result["stop_confirmed"] is True
    assert result["effects_undone"] is False


def test_a_foreign_change_after_the_replace_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone else's bytes in the same window stay, and the update is refused."""
    report = tmp_path / "private" / "outcome.json"
    observation = WorkerObservation(report)
    real_sync = sync_directory

    def sync_then_replace(directory: Path) -> None:
        real_sync(directory)
        report.write_bytes(b"FOREIGN_RECORD")

    monkeypatch.setattr("synapse_channel.worker_outcome.sync_directory", sync_then_replace)
    with pytest.raises(ValueError, match=_REFUSAL):
        observation.observe(OutcomeState.STARTED, "provider_started")
    assert report.read_bytes() == b"FOREIGN_RECORD"
    assert [path.name for path in report.parent.iterdir()] == ["outcome.json"]


def test_bytes_already_at_the_path_are_the_publishers_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path that already holds this publish's bytes is not treated as a stranger."""
    monkeypatch.setattr("synapse_channel.worker_outcome.time.time", lambda: 1_700_000_000.0)
    report = tmp_path / "private" / "outcome.json"
    observation = WorkerObservation(report)
    own = report.read_bytes()
    calls = 0
    real_replace = os.replace

    def counting_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal calls
        calls += 1
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", counting_replace)
    observation.published = b"stale-memory"
    observation.publish()
    assert calls == 0
    assert report.read_bytes() == own
    assert observation.published == own


@pytest.mark.skipif(os.name != "posix", reason="only POSIX keeps the replaced inode open")
def test_an_interrupt_keeps_the_watch_on_the_replaced_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late write into the inode replaced before the interrupt is still restored."""
    report = tmp_path / "private" / "outcome.json"
    before = _descriptors()
    observation = WorkerObservation(report)
    provider = os.open(report, os.O_WRONLY)
    real_replace = os.replace
    fired = False

    def replace_once_then_interrupt(source: str | Path, destination: str | Path) -> None:
        nonlocal fired
        real_replace(source, destination)
        if not fired:
            fired = True
            raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", replace_once_then_interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            observation.observe(OutcomeState.STARTED, "provider_started")
        os.ftruncate(provider, 0)
        os.lseek(provider, 0, os.SEEK_SET)
        os.write(provider, b"LATE_WRITE_INTO_THE_FIRST_INODE")
    finally:
        os.close(provider)
    with pytest.raises(ValueError, match=_REFUSAL):
        observation.observe(OutcomeState.UNKNOWN, "provider_wait_interrupted")
    assert report.read_bytes() == b"LATE_WRITE_INTO_THE_FIRST_INODE"
    assert _descriptors() == before


def test_a_missing_record_in_the_window_is_not_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory sync that removes the record is a refusal, not an owned publish."""
    report = tmp_path / "private" / "outcome.json"
    observation = WorkerObservation(report)
    before = observation.published
    real_sync = sync_directory

    def sync_then_remove(directory: Path) -> None:
        real_sync(directory)
        report.unlink()

    monkeypatch.setattr("synapse_channel.worker_outcome.sync_directory", sync_then_remove)
    with pytest.raises(ValueError, match=_REFUSAL):
        observation.observe(OutcomeState.STARTED, "provider_started")
    assert not report.exists()
    assert observation.published == before

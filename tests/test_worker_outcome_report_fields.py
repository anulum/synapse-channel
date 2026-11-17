# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the worker report reader refuses each malformed field without echoing it

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import worker_outcome
from synapse_channel.launch_record import process_identity
from synapse_channel.worker_outcome import WorkerObservation, read_worker_outcome

_UNTRUSTED = "UNTRUSTED_REPORT_VALUE"


def _report(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """Write a real report through the launcher's own path and return it."""
    report = tmp_path / "private" / "outcome.json"
    WorkerObservation(report).tmux_result(lambda: 0)
    return report, json.loads(report.read_bytes())


def _rewrite(report: Path, document: dict[str, Any]) -> None:
    """Replace the report's bytes in place, keeping its owner-only mode."""
    descriptor = os.open(report, os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(json.dumps(document).encode())


def _with_launcher(document: dict[str, Any], **fields: object) -> dict[str, Any]:
    return {"launcher": {**document["launcher"], **fields}}


_DOCUMENT_REFUSALS: tuple[tuple[str, dict[str, object], str], ...] = (
    ("evidence class", {"evidence_class": _UNTRUSTED}, "invalid worker outcome document"),
    ("schema", {"schema": _UNTRUSTED}, "unsupported worker outcome report"),
    ("boundary", {"boundary": _UNTRUSTED}, "unsupported worker outcome report"),
    ("state not text", {"state": 1}, "invalid worker outcome state"),
    ("state not closed", {"state": _UNTRUSTED}, "invalid worker outcome state"),
    ("operation id not text", {"operation_id": 7}, "invalid worker outcome text"),
    ("observation missing", {"observation": None}, "invalid worker outcome text"),
    ("external effect not closed", {"external_effect": _UNTRUSTED}, "invalid worker outcome state"),
    ("stop requested as integer", {"stop_requested": 1}, "invalid worker outcome stop/undo fact"),
    ("stop confirmed missing", {"stop_confirmed": None}, "invalid worker outcome stop/undo fact"),
    (
        "effects undone as text",
        {"effects_undone": _UNTRUSTED},
        "invalid worker outcome stop/undo fact",
    ),
    ("return code as boolean", {"returncode": True}, "invalid worker outcome return code"),
    ("return code as text", {"returncode": _UNTRUSTED}, "invalid worker outcome return code"),
    ("return code above range", {"returncode": 2**32}, "invalid worker outcome return code"),
    ("return code below range", {"returncode": -(2**31) - 1}, "invalid worker outcome return code"),
    (
        "done with a failing code",
        {"state": "confirmed_done", "returncode": 1},
        "inconsistent worker process completion",
    ),
    (
        "failed with code zero",
        {"state": "confirmed_failed", "returncode": 0},
        "inconsistent worker process failure",
    ),
    (
        "failed with no code",
        {"state": "confirmed_failed", "returncode": None},
        "inconsistent worker process failure",
    ),
    ("time as boolean", {"recorded_at": True}, "invalid worker outcome observation time"),
    ("time zero", {"recorded_at": 0}, "invalid worker outcome observation time"),
    ("time after 9999", {"recorded_at": 253402300800}, "invalid worker outcome observation time"),
    ("time as text", {"recorded_at": _UNTRUSTED}, "invalid worker outcome observation time"),
    ("launcher not an object", {"launcher": _UNTRUSTED}, "invalid worker launcher reference"),
)

_LAUNCHER_REFUSALS: tuple[tuple[str, dict[str, object], str], ...] = (
    ("PID zero", {"pid": 0}, "invalid worker launcher PID"),
    ("PID boolean", {"pid": True}, "invalid worker launcher PID"),
    ("PID too large", {"pid": 2**32}, "invalid worker launcher PID"),
    ("boot not text", {"boot": 1}, "invalid worker launcher birth reference"),
    ("start not text", {"started": 5}, "invalid worker launcher birth reference"),
    ("boot not a UUID", {"boot": _UNTRUSTED}, "invalid worker launcher boot reference"),
    (
        "boot not canonical",
        {"boot": "DC745582-26FE-4462-BE2D-893B2ADF7198"},
        "invalid worker launcher boot reference",
    ),
    ("start not decimal", {"started": "12a"}, "invalid worker launcher start reference"),
    ("start too long", {"started": "1" * 33}, "invalid worker launcher start reference"),
    ("start in non-ASCII digits", {"started": "١٢٣"}, "invalid worker launcher start reference"),
)


@pytest.mark.parametrize(
    ("change", "message"),
    [(change, message) for _name, change, message in _DOCUMENT_REFUSALS],
    ids=[name for name, _change, _message in _DOCUMENT_REFUSALS],
)
def test_each_malformed_report_field_is_refused_without_its_content(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    report, document = _report(tmp_path)
    _rewrite(report, {**document, **change})
    with pytest.raises(ValueError, match=f"^{message}$") as refused:
        read_worker_outcome(report)
    assert _UNTRUSTED not in str(refused.value)


@pytest.mark.parametrize(
    ("change", "message"),
    [(change, message) for _name, change, message in _LAUNCHER_REFUSALS],
    ids=[name for name, _change, _message in _LAUNCHER_REFUSALS],
)
def test_each_malformed_launcher_reference_is_refused_without_its_content(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    report, document = _report(tmp_path)
    _rewrite(report, {**document, **_with_launcher(document, **change)})
    with pytest.raises(ValueError, match=f"^{message}$") as refused:
        read_worker_outcome(report)
    assert _UNTRUSTED not in str(refused.value)


@pytest.mark.parametrize(
    ("change", "state", "returncode"),
    [
        ({"state": "confirmed_done", "returncode": 0}, "confirmed_done", 0),
        ({"state": "confirmed_failed", "returncode": 3}, "confirmed_failed", 3),
        ({"returncode": None}, "unknown", None),
        ({"returncode": -(2**31)}, "unknown", -(2**31)),
        ({"returncode": 2**32 - 1}, "unknown", 2**32 - 1),
    ],
)
def test_a_consistent_report_reads_back_its_state_and_code(
    tmp_path: Path, change: dict[str, object], state: str, returncode: int | None
) -> None:
    report, document = _report(tmp_path)
    _rewrite(report, {**document, **change})
    read = read_worker_outcome(report)
    assert read["state"] == state
    assert read["returncode"] == returncode


def test_an_empty_birth_reference_is_read_as_given(tmp_path: Path) -> None:
    report, document = _report(tmp_path)
    _rewrite(report, {**document, **_with_launcher(document, boot="", started="")})
    read = read_worker_outcome(report)
    assert read["launcher"] == {"pid": document["launcher"]["pid"], "boot": "", "started": ""}


def test_a_started_report_stays_started_only_for_its_live_launcher(tmp_path: Path) -> None:
    """A started report is confirmed only by its launcher's own live start identity.

    Linux and Windows read a process's start identity. Another host, such as
    macOS, reads none, so there a started report is never confirmed and reads
    as unknown.
    """
    report, document = _report(tmp_path)
    live = process_identity(os.getpid())
    reads_start = sys.platform.startswith("linux") or sys.platform == "win32"
    assert bool(live.started) is reads_start
    started = {
        **document,
        "state": "started",
        "external_effect": "unknown",
        "returncode": None,
        "launcher": {"pid": os.getpid(), "boot": live.boot, "started": live.started},
    }
    _rewrite(report, started)
    assert read_worker_outcome(report)["state"] == ("started" if reads_start else "unknown")
    for launcher in (
        {"pid": os.getpid(), "boot": live.boot, "started": ""},
        {"pid": os.getpid(), "boot": live.boot, "started": live.started + "1"},
    ):
        _rewrite(report, {**started, "launcher": launcher})
        read = read_worker_outcome(report)
        assert read["state"] == "unknown"
        assert read["observation"] == "launcher_unconfirmed"


def test_a_first_write_that_fails_closes_its_new_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[int] = []

    def full_disk(descriptor: int, data: bytes) -> None:
        written.append(descriptor)
        raise OSError("no space left on the device")

    monkeypatch.setattr(worker_outcome, "_write_durably", full_disk)
    with pytest.raises(OSError, match="no space left"):
        WorkerObservation(tmp_path / "private" / "outcome.json").tmux_result(lambda: 0)
    assert len(written) == 1
    with pytest.raises(OSError):
        os.fstat(written[0])

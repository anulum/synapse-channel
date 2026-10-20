# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the record of what a launcher started
"""Launch records made from real files and real processes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.private_dir import PrivateDirError
from synapse_channel.core.secure_path import (
    SecurePathError,
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
)
from synapse_channel.launch_record import (
    EVIDENCE_CLASS,
    SCHEMA,
    FileIdentity,
    LaunchRecordError,
    executable_identity,
    file_identity,
    make_record,
    process_identity,
    record_dir_for,
    write_record,
)

LINUX = sys.platform.startswith("linux")
WINDOWS = sys.platform == "win32"


@pytest.fixture
def sleeper() -> Iterator[subprocess.Popen[bytes]]:
    """Start a real process that waits to be told to end."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    try:
        yield process
    finally:
        process.communicate(timeout=30)


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_a_file_is_identified_by_the_bytes_read_from_one_open_file(tmp_path: Path) -> None:
    target = tmp_path / "tool.bin"
    target.write_bytes(b"\x7fELF" + b"x" * 3_000_000)
    link = tmp_path / "tool"
    link.symlink_to(target)

    found = file_identity(link)

    assert found == FileIdentity(str(target.resolve()), 3_000_004, _sha256(target), "")
    assert file_identity(target, shown="as-named").path == "as-named"
    assert file_identity(tmp_path / "empty-not-there") == FileIdentity(
        str((tmp_path / "empty-not-there").resolve()), -1, "", "cannot be opened: FileNotFoundError"
    )
    (tmp_path / "empty").write_bytes(b"")
    assert file_identity(tmp_path / "empty").sha256 == hashlib.sha256(b"").hexdigest()
    too_large = file_identity(target, limit=3_000_003)
    assert (too_large.size, too_large.sha256, too_large.unavailable) == (
        3_000_004,
        "",
        "larger than the limit",
    )
    assert file_identity(target, limit=3_000_004).sha256 == found.sha256
    if LINUX:
        # procfs reports zero size for regular files whose logical content is nonempty.
        virtual = file_identity("/proc/self/status", limit=64)
        assert (virtual.size, virtual.sha256, virtual.unavailable) == (
            -1,
            "",
            "larger than the limit",
        )


@pytest.mark.skipif(WINDOWS, reason="a directory cannot be opened for reading on Windows")
def test_something_that_is_not_a_regular_file_is_not_hashed(tmp_path: Path) -> None:
    pipe = tmp_path / "pipe"
    os.mkfifo(pipe)
    open_before = len(os.listdir("/proc/self/fd")) if LINUX else 0

    for path in (tmp_path, pipe):
        found = file_identity(path)
        assert (found.size, found.sha256, found.unavailable) == (-1, "", "not a regular file")

    # Neither refusal leaves a descriptor open, and the pipe did not make the call wait.
    assert not LINUX or len(os.listdir("/proc/self/fd")) == open_before


@pytest.mark.skipif(not LINUX, reason="reads the process table of Linux")
def test_a_process_of_another_account_keeps_its_start_identity_without_an_image() -> None:
    if os.geteuid() == 0:
        pytest.skip("the administrator can read every process's image")

    first = process_identity(1)

    assert first.started.isdigit() and first.boot and first.unavailable == ""
    assert first.image == FileIdentity("", -1, "", "cannot be named: PermissionError")


def test_the_first_word_candidate_uses_the_launch_search_path(tmp_path: Path) -> None:
    name = "launch-probe.cmd" if WINDOWS else "launch-probe"
    script = tmp_path / "bin" / name
    script.parent.mkdir()
    script.write_text("@echo off\n" if WINDOWS else "#!/bin/sh\nexit 0\n", encoding="ascii")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": str(script.parent), "PATHEXT": ".CMD"}

    by_name = executable_identity(["launch-probe", "--token", "secret"], env)
    by_path = executable_identity([str(script)], {"PATH": ""})
    missing = executable_identity(["launch-probe"], {"PATH": str(tmp_path)})

    assert (
        by_name
        == by_path
        == FileIdentity(str(script.resolve()), script.stat().st_size, _sha256(script), "")
    )
    assert missing == FileIdentity("launch-probe", -1, "", "not found on the search path")
    if os.name == "posix":
        assert executable_identity(["./bin/launch-probe"], cwd=tmp_path) == by_path
        assert executable_identity(["launch-probe"], {"PATH": "bin"}, cwd=tmp_path) == by_path
        assert executable_identity(["launch-probe"], {"PATH": ""}, cwd=script.parent) == by_path
    assert executable_identity([sys.executable]).sha256 == _sha256(os.path.realpath(sys.executable))
    for empty in ([], [""]):
        with pytest.raises(LaunchRecordError, match="a launch record needs a command"):
            executable_identity(empty)


def test_missing_path_uses_the_native_default_executable_search() -> None:
    command = "cmd" if WINDOWS else "sh"
    found = executable_identity([command], {})
    assert not found.unavailable
    assert Path(found.path).is_file()
    assert found.sha256 == _sha256(found.path)


@pytest.mark.skipif(not LINUX, reason="checks real Linux exec fallback and process image")
def test_a_launch_record_candidate_can_differ_from_the_file_that_started(tmp_path: Path) -> None:
    """An invalid first PATH candidate is recorded while a later script actually runs."""
    first = tmp_path / "a" / "probe"
    later = tmp_path / "b" / "probe"
    first.parent.mkdir()
    later.parent.mkdir()
    first.write_text("not an executable format\n", encoding="ascii")
    later.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "print('later candidate running', flush=True)\n"
        "sys.stdin.read()\n"
        "sys.exit(23)\n",
        encoding="utf-8",
    )
    first.chmod(0o700)
    later.chmod(0o700)
    env = {"PATH": os.pathsep.join([str(first.parent), str(later.parent)])}
    process = subprocess.Popen(["probe"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        record = make_record(
            seat="candidate-probe", command=["probe"], pid=process.pid, environ=env
        )
        assert record.executable.path == str(first.resolve())
        assert record.executable.sha256 == _sha256(first)
        assert record.process.image is not None
        assert record.process.image.sha256 == _sha256(os.path.realpath(sys.executable))
        assert record.process.image.path != record.executable.path
    finally:
        output, _ = process.communicate(timeout=30)
    assert output == b"later candidate running\n"
    assert process.returncode == 23


@pytest.mark.skipif(
    not (LINUX or WINDOWS), reason="a start identity is read on Linux and Windows only"
)
def test_a_running_process_has_a_start_identity_and_an_image(
    sleeper: subprocess.Popen[bytes],
) -> None:
    first = process_identity(sleeper.pid)
    second = process_identity(sleeper.pid)
    own = process_identity(os.getpid())

    assert first == second and first.pid == sleeper.pid and first.unavailable == ""
    assert first.started.isdigit() and own.started.isdigit()
    assert (first.pid, first.started) != (own.pid, own.started)
    assert first.image is not None and first.image.sha256 == _sha256(first.image.path)
    assert Path(first.image.path).resolve() == Path(os.path.realpath(sys.executable)).resolve()
    assert bool(first.boot) is LINUX and first.boot == own.boot
    not_numbers: tuple[Any, ...] = (0, -1, True, "12")
    for bad in not_numbers:
        with pytest.raises(LaunchRecordError, match="a positive whole number"):
            process_identity(bad)


def test_a_process_that_has_gone_is_recorded_as_unavailable() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=30)

    gone = process_identity(process.pid)

    assert (gone.pid, gone.boot, gone.started, gone.image) == (process.pid, "", "", None)
    assert gone.unavailable != ""


@pytest.mark.skipif(not LINUX, reason="reads the kernel's link to a running program")
def test_the_image_is_the_program_that_runs_even_after_its_file_was_replaced(
    tmp_path: Path,
) -> None:
    copy = tmp_path / "python-copy"
    copy.write_bytes(Path(os.path.realpath(sys.executable)).read_bytes())
    copy.chmod(0o700)
    original = _sha256(copy)
    process = subprocess.Popen(
        [str(copy), "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    try:
        before = process_identity(process.pid)
        copy.unlink()
        copy.write_bytes(b"#!/bin/sh\necho replaced\n")
        after = process_identity(process.pid)
    finally:
        process.communicate(timeout=30)

    assert before.image is not None and after.image is not None
    assert before.image.sha256 == after.image.sha256 == original != _sha256(copy)
    assert before.image.path == str(copy) and after.image.path == f"{copy} (deleted)"
    assert (before.boot, before.started) == (after.boot, after.started)


@pytest.mark.skipif(not LINUX, reason="a zombie is a state of the Linux process table")
def test_a_process_that_exited_and_was_not_collected_is_not_given_an_identity() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = datetime.now(timezone.utc).timestamp() + 30
        state = ""
        while state != "Z" and datetime.now(timezone.utc).timestamp() < deadline:
            text = Path(f"/proc/{process.pid}/stat").read_text(encoding="utf-8")
            state = text.rsplit(")", 1)[1].split()[0]
        found = process_identity(process.pid)
    finally:
        process.wait(timeout=30)

    assert state == "Z"
    assert (found.started, found.image, found.unavailable) == ("", None, "the process has exited")


def test_a_record_keeps_the_command_as_a_name_a_count_and_a_digest(
    sleeper: subprocess.Popen[bytes], tmp_path: Path
) -> None:
    command = [sys.executable, "--api-key", "sk-live-do-not-store", "run"]
    moment = datetime(2026, 10, 12, 12, 0, 0, 123456, tzinfo=timezone.utc)

    record = make_record(seat=" GROUP/seat-1 ", command=command, pid=sleeper.pid, now=moment)
    data = record.to_json()

    assert data["schema"] == SCHEMA == "session-launch-record-v1"
    assert data["evidence_class"] == EVIDENCE_CLASS
    assert (record.seat, record.recorded_at) == ("GROUP/seat-1", "2026-10-12T12:00:00.123456Z")
    assert (record.command_name, record.command_words) == (os.path.basename(sys.executable), 4)
    assert (
        record.command_sha256
        == hashlib.sha256(
            json.dumps(command, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
    )
    assert record.executable.sha256 == _sha256(os.path.realpath(sys.executable))
    assert record.process == process_identity(sleeper.pid)
    assert (record.launcher_pid, record.system) == (os.getpid(), sys.platform)
    whole = json.dumps(data)
    assert "sk-live-do-not-store" not in whole and "--api-key" not in whole
    assert make_record(seat="s", command=command, pid=sleeper.pid).recorded_at.endswith("Z")
    # What a caller states about vendor and key is kept as stated, and is empty by default.
    assert (record.declared_vendor, record.seat_public_key) == ("", "")
    stated = make_record(
        seat="s",
        command=command,
        pid=sleeper.pid,
        declared_vendor=" example-vendor ",
        seat_public_key="ed25519:key-7",
    )
    assert (stated.declared_vendor, stated.seat_public_key) == ("example-vendor", "ed25519:key-7")
    assert stated.to_json()["evidence_class"] == EVIDENCE_CLASS
    with pytest.raises(LaunchRecordError, match="the declared vendor is 1 to 256 characters"):
        make_record(seat="s", command=command, pid=sleeper.pid, declared_vendor="a\x1bb")
    with pytest.raises(LaunchRecordError, match="the key reference is 1 to 256 characters"):
        make_record(seat="s", command=command, pid=sleeper.pid, seat_public_key="k" * 257)
    for fields, message in (
        ({"seat": "", "command": command}, "the seat is 1 to 256 characters"),
        ({"seat": "a\nb", "command": command}, "the seat is 1 to 256 characters"),
        ({"seat": "s" * 257, "command": command}, "the seat is 1 to 256 characters"),
        ({"seat": "s", "command": []}, "a launch record needs a command"),
        ({"seat": "s", "command": [sys.executable, 5]}, "a command is a list of texts"),
    ):
        with pytest.raises(LaunchRecordError, match=message):
            make_record(pid=sleeper.pid, **fields)
    with pytest.raises(LaunchRecordError, match="a positive whole number"):
        make_record(seat="s", command=command, pid=0)


def test_each_record_is_a_new_owner_only_file_that_is_never_replaced(
    sleeper: subprocess.Popen[bytes], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moment = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
    record = make_record(seat="GROUP/seat 1", command=[sys.executable], pid=sleeper.pid, now=moment)
    directory = tmp_path / "state" / "launch-records"
    # The directory is there already and open to others, as a directory made
    # without care is: by its mode where modes rule, and on Windows by what it
    # inherits from a parent the local users group may read.
    directory.parent.mkdir()
    if WINDOWS:
        subprocess.run(
            ["icacls", str(directory.parent), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"],
            check=True,
            capture_output=True,
        )
    directory.mkdir()
    directory.chmod(0o755)
    with pytest.raises(SecurePathError, match="accessible by other"):
        assert_owner_only_dir_path(directory, purpose="launch record directory")

    first = write_record(directory, record)
    second = write_record(directory, record)

    assert first != second and sorted(directory.iterdir()) == sorted([first, second])
    assert first.name.startswith(f"GROUP_seat_1__20261012T120000000000Z__{sleeper.pid}__")
    assert json.loads(first.read_text(encoding="utf-8")) == json.loads(json.dumps(record.to_json()))
    assert_owner_only_dir_path(directory, purpose="launch record directory")
    for written in (first, second):
        assert_owner_only_file_path(written, purpose="launch record", require_single_link=True)
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="ascii")
    with pytest.raises(PrivateDirError, match="launch record directory"):
        write_record(blocked, record)
    with pytest.raises(PrivateDirError, match="launch record directory"):
        write_record(blocked / "below" / "launch-records", record)
    assert blocked.read_text(encoding="ascii") == "x"


def test_records_are_kept_where_the_environment_says_or_nowhere(tmp_path: Path) -> None:
    under_home = tmp_path / "h" / ".local" / "state" / "synapse" / "launch-records"

    assert (
        record_dir_for({"XDG_STATE_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path / "h")})
        == tmp_path / "xdg" / "synapse" / "launch-records"
    )
    assert record_dir_for({"HOME": str(tmp_path / "h")}) == under_home
    assert record_dir_for({"USERPROFILE": str(tmp_path / "h")}) == under_home
    assert record_dir_for({"XDG_STATE_HOME": " ", "HOME": ""}) is None
    assert record_dir_for({}) is None

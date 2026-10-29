# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real durable writer sequence tests
"""Exercise retained request sequencing across restarts and OS processes."""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from synapse_channel.core.protected_write_sequence import ProtectedWriterSequence


def _open(
    root: Path, *, create: bool = False, key_id: str = "writer-key"
) -> ProtectedWriterSequence:
    """Open the public counter while retaining ownership of the borrowed root."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        identity = root.stat()
        return ProtectedWriterSequence(
            descriptor,
            root_identity=(identity.st_dev, identity.st_ino),
            name="writer.sequence",
            owner_uid=os.geteuid(),
            key_id=key_id,
            sender="EXAMPLE/writer",
            create=create,
        )
    finally:
        os.close(descriptor)


def _allocate(root: Path, count: int) -> list[int]:
    """Allocate actual durable values in an independent worker process."""
    with _open(root) as sequence:
        return [sequence.next() for _ in range(count)]


def test_sequence_survives_restart_and_concurrent_processes(tmp_path: Path) -> None:
    """Every process receives unique increasing values from one retained inode."""
    with _open(tmp_path, create=True) as sequence:
        assert [sequence.next(), sequence.next()] == [1, 2]
    with _open(tmp_path) as reopened:
        assert reopened.next() == 3
    with ProcessPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(_allocate, [tmp_path] * 4, [10] * 4))
    assert sorted(value for batch in batches for value in batch) == list(range(4, 44))
    with _open(tmp_path) as reopened:
        assert reopened.next() == 44


def test_sequence_serializes_threads_on_one_descriptor(tmp_path: Path) -> None:
    """A process may share its sequence callback without duplicate values."""
    with _open(tmp_path, create=True) as sequence:
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sorted(pool.map(lambda _item: sequence.next(), range(64))) == list(range(1, 65))


def test_sequence_rejects_reinitialization_and_wrong_credential(tmp_path: Path) -> None:
    """A restarted writer cannot silently reset or adopt another key's floor."""
    with _open(tmp_path, create=True) as sequence:
        assert sequence.next() == 1
    with pytest.raises(FileExistsError):
        _open(tmp_path, create=True)
    with pytest.raises(ValueError, match="credential binding"):
        _open(tmp_path, key_id="other-key")


def test_sequence_refuses_path_replacement_and_damage(tmp_path: Path) -> None:
    """A look-alike file or damaged retained bytes never emits a new sequence."""
    with _open(tmp_path, create=True) as sequence:
        assert sequence.next() == 1
        original = tmp_path / "writer.sequence"
        moved = tmp_path / "old.sequence"
        original.rename(moved)
        original.write_bytes(moved.read_bytes())
        original.chmod(0o600)
        with pytest.raises(ValueError, match="attachment changed"):
            sequence.next()
    original.unlink()
    moved.rename(original)
    with _open(tmp_path) as sequence:
        descriptor = os.open(original, os.O_RDWR | os.O_NOFOLLOW)
        try:
            os.pwrite(descriptor, b"BADSEQ1\0", 0)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        with pytest.raises(ValueError, match="missing or damaged"):
            sequence.next()
    with pytest.raises(ValueError, match="credential binding"):
        _open(tmp_path)


def test_sequence_refuses_unsafe_layout_and_exhaustion(tmp_path: Path) -> None:
    """Private ownership and bounded JSON-safe counters are mandatory."""
    with _open(tmp_path, create=True) as sequence:
        file = tmp_path / "writer.sequence"
        file.chmod(0o644)
        with pytest.raises(ValueError, match="ownership or type"):
            sequence.next()
        file.chmod(0o600)
        root = tmp_path.stat()
        tmp_path.chmod(0o755)
        with pytest.raises(ValueError, match="directory changed"):
            sequence.next()
        tmp_path.chmod(0o700)
        descriptor = os.open(file, os.O_RDWR | os.O_NOFOLLOW)
        try:
            body = file.read_bytes()[:40] + struct.pack(">Q", (1 << 53) - 1)
            assert os.pwrite(descriptor, body + hashlib.sha256(body).digest(), 0) == 80
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        with pytest.raises(ValueError, match="budget exhausted"):
            sequence.next()
        assert (tmp_path.stat().st_dev, tmp_path.stat().st_ino) == (root.st_dev, root.st_ino)
    with pytest.raises(ValueError, match="closed"):
        sequence.next()
    sequence.close()


def test_sequence_rejects_invalid_enrollment(tmp_path: Path) -> None:
    """Descriptor identity, basename and dedicated owner are checked on open."""
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        info = tmp_path.stat()
        with pytest.raises(ValueError, match="invalid protected writer sequence enrollment"):
            ProtectedWriterSequence(
                descriptor,
                root_identity=(info.st_dev, info.st_ino),
                name="../writer.sequence",
                owner_uid=os.geteuid(),
                key_id="writer-key",
                sender="EXAMPLE/writer",
            )
        with pytest.raises(ValueError, match="directory identity"):
            ProtectedWriterSequence(
                descriptor,
                root_identity=(info.st_dev, info.st_ino + 1),
                name="writer.sequence",
                owner_uid=os.geteuid(),
                key_id="writer-key",
                sender="EXAMPLE/writer",
            )
    finally:
        os.close(descriptor)


def _limited_sequence(
    root: Path, phase: str, file_limit: int = 47
) -> subprocess.CompletedProcess[str]:
    """Reach the public counter with a real file-size limit in a disposable child."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, resource, signal, sys
from synapse_channel.core.protected_write_sequence import ProtectedWriterSequence

root = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
info = os.fstat(root)
before = len(os.listdir('/proc/self/fd'))
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
original_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
resource.setrlimit(resource.RLIMIT_FSIZE, (int(sys.argv[3]), original_limit[1]))
try:
    try:
        with ProtectedWriterSequence(
            root, root_identity=(info.st_dev, info.st_ino), name='writer.sequence',
            owner_uid=os.geteuid(), key_id='writer-key', sender='EXAMPLE/writer',
            create=sys.argv[2] == 'initialization',
        ) as sequence:
            sequence.next()
    except OSError as error:
        assert str(error) == 'incomplete writer sequence ' + sys.argv[2], str(error)
    else:
        raise AssertionError('short durable write emitted a sequence')
    assert len(os.listdir('/proc/self/fd')) == before
    assert os.fstat(root).st_ino == info.st_ino
finally:
    resource.setrlimit(resource.RLIMIT_FSIZE, original_limit)
    os.close(root)
print('refused')
""",
            str(root),
            phase,
            str(file_limit),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )


def test_sequence_refuses_short_durable_writes(tmp_path: Path) -> None:
    """Real truncated writes preserve damaged initialization and an unissued increment."""
    initial = tmp_path / "initial"
    initial.mkdir(mode=0o700)
    result = _limited_sequence(initial, "initialization")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "refused\n"
    damaged = (initial / "writer.sequence").read_bytes()
    assert len(damaged) == 47
    with pytest.raises(ValueError, match="missing or damaged"):
        _open(initial)
    assert (initial / "writer.sequence").read_bytes() == damaged

    later = tmp_path / "later"
    later.mkdir(mode=0o700)
    with _open(later, create=True) as sequence:
        assert sequence.next() == 1
    original = (later / "writer.sequence").read_bytes()
    result = _limited_sequence(later, "write")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "refused\n"
    assert (later / "writer.sequence").read_bytes() == original
    with _open(later) as sequence:
        assert sequence.next() == 2
    with _open(later) as sequence:
        assert sequence.next() == 3


def test_partial_counter_update_cannot_reopen_with_an_older_checksum(tmp_path: Path) -> None:
    """A real short write of the new counter without its checksum fails closed."""
    with _open(tmp_path, create=True) as sequence:
        assert sequence.next() == 1
    before = (tmp_path / "writer.sequence").read_bytes()
    result = _limited_sequence(tmp_path, "write", file_limit=48)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "refused\n"
    damaged = (tmp_path / "writer.sequence").read_bytes()
    assert len(damaged) == 80 and damaged[:40] == before[:40]
    assert struct.unpack(">Q", damaged[40:48])[0] == 2
    assert damaged[48:] == before[48:]
    with pytest.raises(ValueError, match="missing or damaged"):
        _open(tmp_path)
    assert (tmp_path / "writer.sequence").read_bytes() == damaged


@pytest.mark.parametrize("changed", ["counter", "checksum", "truncated", "trailing-byte"])
def test_damaged_record_never_issues_a_reused_sequence(tmp_path: Path, changed: str) -> None:
    """Actual lower-counter and malformed file bytes cannot produce another value."""
    with _open(tmp_path, create=True) as sequence:
        assert sequence.next() == 1
        file = tmp_path / "writer.sequence"
        original = file.read_bytes()
        if changed == "counter":
            damaged = original[:40] + struct.pack(">Q", 0) + original[48:]
        elif changed == "checksum":
            damaged = original[:-1] + bytes([original[-1] ^ 1])
        elif changed == "truncated":
            damaged = original[:-1]
        else:
            damaged = original + b"x"
        file.write_bytes(damaged)
        with pytest.raises(ValueError, match="missing or damaged"):
            sequence.next()
    with pytest.raises(ValueError, match="missing or damaged"):
        _open(tmp_path)
    assert file.read_bytes() == damaged


@pytest.mark.parametrize("checksum", ["before", "after"])
def test_torn_carry_cannot_reset_counter_to_zero(tmp_path: Path, checksum: str) -> None:
    """The real 255-to-256 carry refuses a lower mixed value with either checksum."""
    with _open(tmp_path, create=True):
        file = tmp_path / "writer.sequence"
        body = file.read_bytes()[:40] + struct.pack(">Q", 255)
        file.write_bytes(body + hashlib.sha256(body).digest())
    before = file.read_bytes()
    with _open(tmp_path) as sequence:
        assert sequence.next() == 256
        after = file.read_bytes()
        chosen = before if checksum == "before" else after
        damaged = chosen[:40] + struct.pack(">Q", 0) + chosen[48:]
        file.write_bytes(damaged)
        with pytest.raises(ValueError, match="missing or damaged"):
            sequence.next()
    with pytest.raises(ValueError, match="missing or damaged"):
        _open(tmp_path)
    assert file.read_bytes() == damaged


def test_real_fsync_refusal_never_emits_the_written_value(tmp_path: Path) -> None:
    """A child-only kernel refusal leaves an unissued value and a safe durable gap."""
    with _open(tmp_path, create=True) as sequence:
        assert sequence.next() == 1
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import ctypes, errno, os, pathlib, sys
from synapse_channel.core.protected_write_sequence import ProtectedWriterSequence

root = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
info = os.fstat(root)
sequence = ProtectedWriterSequence(
    root, root_identity=(info.st_dev, info.st_ino), name='writer.sequence',
    owner_uid=os.geteuid(), key_id='writer-key', sender='EXAMPLE/writer',
)
target = pathlib.Path(sys.argv[1], 'writer.sequence').stat()
descriptors = []
for name in os.listdir('/proc/self/fd'):
    try:
        observed = os.fstat(int(name))
    except OSError:
        continue
    if (observed.st_dev, observed.st_ino) == (target.st_dev, target.st_ino):
        descriptors.append(int(name))
assert len(descriptors) == 1
class Comparison(ctypes.Structure):
    _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_uint),
                ('datum_a', ctypes.c_uint64), ('datum_b', ctypes.c_uint64)]
library = ctypes.CDLL('libseccomp.so.2', use_errno=True)
library.seccomp_init.argtypes = [ctypes.c_uint32]
library.seccomp_init.restype = ctypes.c_void_p
library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
library.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
    ctypes.c_int, ctypes.c_uint, ctypes.POINTER(Comparison)]
library.seccomp_load.argtypes = [ctypes.c_void_p]
library.seccomp_release.argtypes = [ctypes.c_void_p]
context = library.seccomp_init(0x7fff0000)
assert context
try:
    syscall = library.seccomp_syscall_resolve_name(b'fsync')
    assert syscall >= 0
    comparison = Comparison(0, 4, descriptors[0], 0)
    assert library.seccomp_rule_add_array(
        context, 0x50000 | errno.EPERM, syscall, 1, ctypes.byref(comparison),
    ) == 0
    assert library.seccomp_load(context) == 0
finally:
    library.seccomp_release(context)
try:
    sequence.next()
except PermissionError as error:
    assert error.errno == errno.EPERM
else:
    raise AssertionError('fsync failure emitted a value')
os.close(root)
# Retain the refused descriptor until child exit: only that actual fd is denied,
# so subprocess coverage storage cannot accidentally inherit its numeric slot.
print('unissued')
""",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "unissued\n"
    with _open(tmp_path) as sequence:
        assert sequence.next() == 3
    with _open(tmp_path) as sequence:
        assert sequence.next() == 4


@pytest.mark.parametrize(
    ("key_id", "sender"),
    [("writer-key", "EXAMPLE/\0writer"), ("writer-key\0EXAMPLE/", "writer")],
    ids=["separator-in-sender", "separator-in-key"],
)
def test_sequence_refuses_ambiguous_credential_enrollment_before_creation(
    tmp_path: Path, key_id: str, sender: str
) -> None:
    """Different key/sender pairs cannot share a delimiter-based counter binding.

    Protected wire identifiers already refuse these control bytes. The standalone
    counter must reject the declaration before creating an unusable private file.
    """
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        info = tmp_path.stat()
        with pytest.raises(ValueError, match="protected writer sequence enrollment"):
            ProtectedWriterSequence(
                descriptor,
                root_identity=(info.st_dev, info.st_ino),
                name="writer.sequence",
                owner_uid=os.geteuid(),
                key_id=key_id,
                sender=sender,
                create=True,
            )
        assert not (tmp_path / "writer.sequence").exists()
    finally:
        os.close(descriptor)

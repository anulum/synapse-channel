# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — same-descriptor owner-only secret file reads
"""Coverage for :func:`synapse_channel.core.secure_path.read_owner_only_file_bytes`."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from synapse_channel.core.at_rest import KEY_BYTES, generate_key_file, load_key_file
from synapse_channel.core.payload_crypto import PayloadCryptoError, load_payload_key
from synapse_channel.core.secure_path import (
    SecurePathError,
    apply_owner_only_file,
    read_owner_only_file_bytes,
)


def _write_secret(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    apply_owner_only_file(path)
    return path


def test_read_owner_only_happy_path_exact_size(tmp_path: Path) -> None:
    path = _write_secret(tmp_path / "k.bin", b"\x01" * KEY_BYTES)
    assert read_owner_only_file_bytes(path, purpose="key file", expected_size=KEY_BYTES) == (
        b"\x01" * KEY_BYTES
    )


def test_policy_read_requires_one_link_without_changing_existing_key_byte_reads(
    tmp_path: Path,
) -> None:
    path = _write_secret(tmp_path / "policy.json", b'{"version":1}')
    assert (
        read_owner_only_file_bytes(
            path, purpose="private policy", max_bytes=64, require_single_link=True
        )
        == path.read_bytes()
    )
    alias = tmp_path / "policy-alias.json"
    os.link(path, alias)
    with pytest.raises(SecurePathError, match="hard links"):
        read_owner_only_file_bytes(
            path, purpose="private policy", max_bytes=64, require_single_link=True
        )
    assert read_owner_only_file_bytes(path, purpose="existing key bytes") == path.read_bytes()
    assert alias.read_bytes() == path.read_bytes()


def test_read_owner_only_refuses_missing(tmp_path: Path) -> None:
    missing = tmp_path / "absent.bin"
    with pytest.raises(SecurePathError, match="does not exist"):
        read_owner_only_file_bytes(missing, purpose="key file", expected_size=KEY_BYTES)


def test_read_owner_only_refuses_wrong_size(tmp_path: Path) -> None:
    path = _write_secret(tmp_path / "short.bin", b"\x00" * 8)
    with pytest.raises(SecurePathError, match="exactly"):
        read_owner_only_file_bytes(path, purpose="key file", expected_size=KEY_BYTES)


@pytest.mark.parametrize("replacement", [b"", b"x" * (KEY_BYTES + 1)])
def test_read_owner_only_refuses_size_changes_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: bytes
) -> None:
    path = _write_secret(tmp_path / "changing.key", b"k" * KEY_BYTES)
    original = path.stat()
    identity = (original.st_dev, original.st_ino)
    native_read = os.read
    read_descriptors: list[int] = []

    with path.open("r+b") as producer:

        def change_file_then_read(descriptor: int, length: int) -> bytes:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) == identity:
                producer.seek(0)
                producer.truncate(0)
                producer.write(replacement)
                producer.flush()
                read_descriptors.append(descriptor)
            return native_read(descriptor, length)

        with monkeypatch.context() as boundary:
            boundary.setattr(os, "read", change_file_then_read)
            with pytest.raises(SecurePathError, match=f"exactly {KEY_BYTES} bytes"):
                read_owner_only_file_bytes(
                    path, purpose="key file", expected_size=KEY_BYTES, require_single_link=True
                )

    assert len(read_descriptors) == 1
    assert path.read_bytes() == replacement
    assert (path.stat().st_dev, path.stat().st_ino) == identity


def test_read_owner_only_refuses_directory(tmp_path: Path) -> None:
    directory = tmp_path / "dir"
    directory.mkdir(mode=0o700)
    with pytest.raises(SecurePathError, match="regular file|cannot open|directory"):
        read_owner_only_file_bytes(directory, purpose="key file", expected_size=KEY_BYTES)


@pytest.mark.skipif(os.name != "posix", reason="symlink O_NOFOLLOW is POSIX-primary")
def test_read_owner_only_refuses_symlink(tmp_path: Path) -> None:
    real = _write_secret(tmp_path / "real.bin", b"\x02" * KEY_BYTES)
    link = tmp_path / "link.bin"
    link.symlink_to(real)
    with pytest.raises(SecurePathError, match="symlink|cannot open"):
        read_owner_only_file_bytes(link, purpose="key file", expected_size=KEY_BYTES)


@pytest.mark.skipif(os.name != "posix", reason="chmod group bits are POSIX")
def test_read_owner_only_refuses_group_readable(tmp_path: Path) -> None:
    path = _write_secret(tmp_path / "loose.bin", b"\x03" * KEY_BYTES)
    os.chmod(path, 0o640)
    with pytest.raises(SecurePathError, match="owner-only|accessible by other"):
        read_owner_only_file_bytes(path, purpose="key file", expected_size=KEY_BYTES)


def test_read_owner_only_max_bytes_cap(tmp_path: Path) -> None:
    path = _write_secret(tmp_path / "big.bin", b"x" * 64)
    with pytest.raises(SecurePathError, match="exceeds"):
        read_owner_only_file_bytes(path, purpose="blob", max_bytes=16)


def test_read_owner_only_reads_past_one_chunk(tmp_path: Path) -> None:
    payload = b"q" * (65_536 + 1)
    path = _write_secret(tmp_path / "wide.bin", payload)
    assert read_owner_only_file_bytes(path, purpose="blob") == payload


def test_read_owner_only_reads_an_empty_file(tmp_path: Path) -> None:
    path = _write_secret(tmp_path / "empty.bin", b"")
    assert read_owner_only_file_bytes(path, purpose="blob") == b""


def test_read_owner_only_refuses_when_the_floor_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.secure_path as module

    path = _write_secret(tmp_path / "k.bin", b"abcd")
    monkeypatch.setattr(module, "owner_only_floor_available", lambda: False)
    with pytest.raises(SecurePathError, match="unavailable"):
        module.read_owner_only_file_bytes(path, purpose="key file")


def test_read_owner_only_uses_the_windows_kind_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.secure_path as module

    path = _write_secret(tmp_path / "k.bin", b"abcd")
    monkeypatch.setattr(module, "_POSIX", False)
    monkeypatch.setattr(module, "_WINDOWS", True)
    monkeypatch.setattr(module, "assert_owner_only_file_path", lambda *args, **kwargs: None)
    assert module.read_owner_only_file_bytes(path, purpose="key file", expected_size=4) == b"abcd"
    directory = tmp_path / "dir"
    directory.mkdir(mode=0o700)
    # Linux can open a directory and then refuse it. Windows refuses the open.
    with pytest.raises(SecurePathError, match="not a regular file|cannot open"):
        module.read_owner_only_file_bytes(directory, purpose="key file", expected_size=0)


def test_read_owner_only_refuses_when_the_read_overruns_the_stat_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that yields more bytes than fstat reported is still capped."""
    import synapse_channel.core.secure_path as module

    path = _write_secret(tmp_path / "grew.bin", b"abcdefghij")
    real_fstat = module.os.fstat  # type: ignore[attr-defined]  # the module imports os without re-exporting it

    def smaller(fd: int) -> object:
        info = real_fstat(fd)
        return info.__class__(list(info)[:6] + [4] + list(info)[7:])

    monkeypatch.setattr(module.os, "fstat", smaller)  # type: ignore[attr-defined]  # the module imports this name without re-exporting it
    with pytest.raises(SecurePathError, match="exceeds the 6-byte"):
        module.read_owner_only_file_bytes(path, purpose="blob", max_bytes=6)


def test_read_owner_only_refuses_a_short_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.secure_path as module

    path = _write_secret(tmp_path / "k.bin", b"abcd")

    def short(_fd: int, _size: int) -> bytes:
        return b"ab"

    monkeypatch.setattr(module.os, "read", short)  # type: ignore[attr-defined]  # the module imports this name without re-exporting it
    with pytest.raises(SecurePathError, match="found 2"):
        module.read_owner_only_file_bytes(path, purpose="key file", expected_size=4)


def test_load_key_file_uses_same_fd_helper(tmp_path: Path) -> None:
    key_path = tmp_path / "at-rest.key"
    generate_key_file(key_path)
    material = load_key_file(key_path)
    assert len(material) == KEY_BYTES
    assert load_key_file(key_path) == material


def test_load_key_file_maps_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        load_key_file(tmp_path / "nope.key")


def test_load_payload_key_maps_wrong_size(tmp_path: Path) -> None:
    path = _write_secret(tmp_path / "payload.key", b"\x04" * 8)
    with pytest.raises(PayloadCryptoError, match="exactly 32 bytes"):
        load_payload_key(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode floor")
def test_load_payload_key_maps_loose_mode(tmp_path: Path) -> None:
    path = _write_secret(tmp_path / "payload.key", b"\x05" * KEY_BYTES)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
    with pytest.raises(PayloadCryptoError, match="owner-only"):
        load_payload_key(path)

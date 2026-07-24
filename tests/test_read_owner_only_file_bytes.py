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


def test_read_owner_only_refuses_missing(tmp_path: Path) -> None:
    missing = tmp_path / "absent.bin"
    with pytest.raises(SecurePathError, match="does not exist"):
        read_owner_only_file_bytes(missing, purpose="key file", expected_size=KEY_BYTES)


def test_read_owner_only_refuses_wrong_size(tmp_path: Path) -> None:
    path = _write_secret(tmp_path / "short.bin", b"\x00" * 8)
    with pytest.raises(SecurePathError, match="exactly"):
        read_owner_only_file_bytes(path, purpose="key file", expected_size=KEY_BYTES)


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

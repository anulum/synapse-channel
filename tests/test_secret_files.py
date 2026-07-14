# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for owner-only secret file loading
"""Exercise the owner-only secret loaders: permissions, shape, and redaction."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secret_files import (
    DEFAULT_SECRET_FILE_LIMIT,
    SecretFileError,
    read_secret_file,
    read_secret_lines,
)


def _secret(tmp_path: Path, content: str, *, mode: int = 0o600) -> Path:
    path = tmp_path / "secret"
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_read_secret_file_strips_surrounding_whitespace(tmp_path: Path) -> None:
    path = _secret(tmp_path, "  bearer-token-value\n")
    assert read_secret_file(path, flag="--metrics-token-file") == "bearer-token-value"


def test_read_secret_file_accepts_owner_only_modes(tmp_path: Path) -> None:
    for mode in (0o600, 0o400):
        path = _secret(tmp_path, "value", mode=mode)
        assert read_secret_file(path, flag="--metrics-token-file") == "value"


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666])
def test_read_secret_file_refuses_group_or_world_access(tmp_path: Path, mode: int) -> None:
    path = _secret(tmp_path, "leakable", mode=mode)

    with pytest.raises(SecretFileError, match="chmod 600") as excinfo:
        read_secret_file(path, flag="--metrics-token-file")

    message = str(excinfo.value)
    assert "leakable" not in message  # the content never appears in the error
    assert "--metrics-token-file" in message
    assert f"{mode:03o}" in message


def test_read_secret_file_missing_file_names_flag_not_content(tmp_path: Path) -> None:
    with pytest.raises(SecretFileError, match="--metrics-token-file"):
        read_secret_file(tmp_path / "absent", flag="--metrics-token-file")


def test_read_secret_file_refuses_an_empty_file(tmp_path: Path) -> None:
    path = _secret(tmp_path, "   \n")
    with pytest.raises(SecretFileError, match="empty"):
        read_secret_file(path, flag="--metrics-token-file")


def test_read_secret_lines_skips_blanks_and_comments(tmp_path: Path) -> None:
    path = _secret(
        tmp_path,
        "# rotated 2026-07-14\nmain:s3cret:ALPHA\n\nnext:s3cret2:ALPHA,BETA\n",
    )
    assert read_secret_lines(path, flag="--message-auth-key-file") == (
        "main:s3cret:ALPHA",
        "next:s3cret2:ALPHA,BETA",
    )


def test_read_secret_lines_refuses_a_file_with_no_entries(tmp_path: Path) -> None:
    path = _secret(tmp_path, "# only a comment\n\n")
    with pytest.raises(SecretFileError, match="no entries"):
        read_secret_lines(path, flag="--message-auth-key-file")


def test_read_secret_lines_refuses_group_readable_file_without_content(tmp_path: Path) -> None:
    path = _secret(tmp_path, "main:s3cret:ALPHA\n", mode=0o644)

    with pytest.raises(SecretFileError) as excinfo:
        read_secret_lines(path, flag="--message-auth-key-file")

    assert "s3cret" not in str(excinfo.value)


def test_read_secret_lines_missing_file_names_flag_not_content(tmp_path: Path) -> None:
    with pytest.raises(SecretFileError, match="--message-auth-key-file"):
        read_secret_lines(tmp_path / "absent", flag="--message-auth-key-file")


@pytest.mark.parametrize("reader", [read_secret_file, read_secret_lines])
def test_secret_file_forms_fail_closed_on_non_posix_platforms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader: Callable[..., object],
) -> None:
    """A platform that cannot prove the owner boundary must refuse the file form."""
    import synapse_channel.core.secret_files as module

    monkeypatch.setattr(module, "_POSIX", False)
    path = _secret(tmp_path, "main:s3cret:ALPHA\n", mode=0o644)
    with pytest.raises(SecretFileError, match="validation is unavailable"):
        reader(path, flag="--message-auth-key-file")


@pytest.mark.parametrize("reader", [read_secret_file, read_secret_lines])
@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem boundary")
def test_secret_readers_refuse_symlinks_and_non_regular_files(
    tmp_path: Path,
    reader: Callable[..., object],
) -> None:
    target = _secret(tmp_path, "target-secret\n")
    link = tmp_path / "secret-link"
    link.symlink_to(target)
    with pytest.raises(SecretFileError, match="securely open"):
        reader(link, flag="--message-auth-key-file")

    fifo = tmp_path / "secret-fifo"
    os.mkfifo(fifo, mode=0o600)
    with pytest.raises(SecretFileError, match="not a regular"):
        reader(fifo, flag="--message-auth-key-file")


@pytest.mark.parametrize("reader", [read_secret_file, read_secret_lines])
@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership boundary")
def test_secret_readers_refuse_foreign_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader: Callable[..., object],
) -> None:
    path = _secret(tmp_path, "owned-secret\n")
    monkeypatch.setattr(
        "synapse_channel.core.secret_files.os.geteuid", lambda: path.stat().st_uid + 1
    )
    with pytest.raises(SecretFileError, match="effective hub service user"):
        reader(path, flag="--message-auth-key-file")


@pytest.mark.parametrize("reader", [read_secret_file, read_secret_lines])
@pytest.mark.skipif(os.name != "posix", reason="POSIX size boundary")
def test_secret_readers_refuse_oversize_files(
    tmp_path: Path,
    reader: Callable[..., object],
) -> None:
    path = _secret(tmp_path, "owned-secret\n")
    path.write_bytes(b"x" * (DEFAULT_SECRET_FILE_LIMIT + 1))
    path.chmod(0o600)
    with pytest.raises(SecretFileError, match="byte secret-file limit"):
        reader(path, flag="--message-auth-key-file")


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor boundary")
def test_secret_reader_enforces_the_limit_after_the_file_grows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _secret(tmp_path, "initial\n")
    original_fstat = os.fstat

    def fstat_then_grow(descriptor: int) -> os.stat_result:
        info = original_fstat(descriptor)
        with path.open("ab") as stream:
            stream.write(b"x" * (DEFAULT_SECRET_FILE_LIMIT + 1))
        return info

    monkeypatch.setattr("synapse_channel.core.secret_files.os.fstat", fstat_then_grow)
    with pytest.raises(SecretFileError, match="byte secret-file limit"):
        read_secret_file(path, flag="--metrics-token-file")


def test_secret_reader_refuses_invalid_utf8_without_echoing_bytes(tmp_path: Path) -> None:
    path = tmp_path / "secret"
    path.write_bytes(b"prefix-\xff-secret")
    path.chmod(0o600)

    with pytest.raises(SecretFileError, match="not valid UTF-8") as excinfo:
        read_secret_file(path, flag="--metrics-token-file")

    assert "prefix" not in str(excinfo.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor boundary")
def test_secret_reader_uses_the_validated_descriptor_after_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _secret(tmp_path, "descriptor-secret\n")
    replacement = tmp_path / "replacement"
    replacement.write_text("replacement-secret\n", encoding="utf-8")
    replacement.chmod(0o600)
    original_open = os.open

    def open_then_replace(file: os.PathLike[str] | str, flags: int) -> int:
        descriptor = original_open(file, flags)
        path.unlink()
        replacement.rename(path)
        return descriptor

    monkeypatch.setattr("synapse_channel.core.secret_files.os.open", open_then_replace)
    assert read_secret_file(path, flag="--metrics-token-file") == "descriptor-secret"


def test_secret_file_error_is_a_synapse_error_with_stable_code(tmp_path: Path) -> None:
    with pytest.raises(SynapseError) as excinfo:
        read_secret_file(tmp_path / "absent", flag="--metrics-token-file")
    assert excinfo.value.code == "secret_file"
    assert isinstance(excinfo.value, ValueError)


def test_read_secret_file_expands_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ~ expansion is part of the contract: operators write ~/secrets/token in units.
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "token"
    path.write_text("home-secret\n", encoding="utf-8")
    path.chmod(0o600)
    assert read_secret_file("~/token", flag="--metrics-token-file") == "home-secret"

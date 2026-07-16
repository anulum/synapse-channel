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
    open_nofollow_descriptor,
    read_regular_file_bytes,
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


def test_component_walker_fails_closed_without_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import synapse_channel.core.secret_files as module

    monkeypatch.setattr(module, "_POSIX", False)
    with pytest.raises(OSError, match="unavailable"):
        open_nofollow_descriptor("secret")


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


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem boundary")
def test_secret_readers_refuse_symlinked_ancestors_and_hardlinks(tmp_path: Path) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    target = real_directory / "secret"
    target.write_text("policy\n", encoding="utf-8")
    target.chmod(0o600)
    ancestor_link = tmp_path / "via-link"
    ancestor_link.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(SecretFileError, match="cannot securely open"):
        read_secret_file(ancestor_link / "secret", flag="--mcp-config")

    hardlink = tmp_path / "second-name"
    os.link(target, hardlink)
    with pytest.raises(SecretFileError, match="hard links"):
        read_secret_file(target, flag="--mcp-config", require_single_link=True)
    assert read_secret_file(target, flag="--token-file") == "policy"


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


def test_secret_reader_wraps_descriptor_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _secret(tmp_path, "secret\n")

    def fail_read(_descriptor: int, _size: int) -> bytes:
        raise OSError("forced read error")

    monkeypatch.setattr(os, "read", fail_read)
    with pytest.raises(SecretFileError, match="cannot securely read"):
        read_secret_file(path, flag="--metrics-token-file")


def test_secret_reader_rejects_descriptor_metadata_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _secret(tmp_path, "secret\n")
    real_fstat = os.fstat
    calls = 0

    def drift_after_read(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        info = real_fstat(descriptor)
        if calls == 2:
            values = list(info)
            values[9] += 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(os, "fstat", drift_after_read)
    with pytest.raises(SecretFileError, match="changed while its policy was being read"):
        read_secret_file(path, flag="--mcp-config", require_single_link=True)


def test_regular_file_reader_covers_public_material_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "public"
    path.write_bytes(b"public-bytes")
    assert read_regular_file_bytes(path, label="public") == b"public-bytes"

    with pytest.raises(SecretFileError, match="byte file limit"):
        read_regular_file_bytes(path, label="public", limit=1)
    with pytest.raises(SecretFileError, match="not a regular file"):
        read_regular_file_bytes(tmp_path, label="public")
    with pytest.raises(SecretFileError, match="cannot securely open"):
        read_regular_file_bytes(tmp_path / "missing", label="public")

    import synapse_channel.core.secret_files as module

    monkeypatch.setattr(module, "_POSIX", False)
    with pytest.raises(SecretFileError, match="unavailable"):
        read_regular_file_bytes(path, label="public")


def test_regular_file_reader_enforces_growth_and_wraps_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "public"
    path.write_bytes(b"x")
    reads = iter((b"xx", b""))
    monkeypatch.setattr(os, "read", lambda _descriptor, _size: next(reads))
    with pytest.raises(SecretFileError, match="byte file limit"):
        read_regular_file_bytes(path, label="public", limit=1)

    monkeypatch.undo()

    def fail_read(_descriptor: int, _size: int) -> bytes:
        raise OSError("forced public read error")

    monkeypatch.setattr(os, "read", fail_read)
    with pytest.raises(SecretFileError, match="cannot securely read"):
        read_regular_file_bytes(path, label="public")


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
    retained = tmp_path / "retained-original"
    original_open = os.open

    replaced = False

    def open_then_replace(
        file: os.PathLike[str] | str, flags: int, *, dir_fd: int | None = None
    ) -> int:
        nonlocal replaced
        descriptor = original_open(file, flags, dir_fd=dir_fd)
        if not replaced and os.fstat(descriptor).st_ino == path.stat().st_ino:
            replaced = True
            path.rename(retained)
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

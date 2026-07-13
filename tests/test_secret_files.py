# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for owner-only secret file loading
"""Exercise the owner-only secret loaders: permissions, shape, and redaction."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secret_files import (
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
def test_permission_check_is_skipped_on_non_posix_platforms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader: Callable[..., object],
) -> None:
    """On a platform without POSIX modes the read proceeds; the check is not expressible.

    A group-readable file that would fail on Linux must still load on Windows, where
    ``os.name != 'posix'`` and the mode bits carry no such meaning.
    """
    import synapse_channel.core.secret_files as module

    monkeypatch.setattr(module, "_POSIX", False)
    path = _secret(tmp_path, "main:s3cret:ALPHA\n", mode=0o644)
    assert reader(path, flag="--message-auth-key-file")


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

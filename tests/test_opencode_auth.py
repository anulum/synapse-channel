# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import base64
import os
import subprocess
from pathlib import Path

import pytest

from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.participants.opencode_auth import (
    OpenCodeAuthError,
    basic_authorization,
    load_password_file,
    validate_endpoint,
)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("http://127.0.0.1:4096/", "http://127.0.0.1:4096"),
        ("http://localhost:4096/api/", "http://localhost:4096/api"),
        ("https://example.test/opencode/", "https://example.test/opencode"),
    ],
)
def test_endpoint_policy_accepts_loopback_http_and_remote_https(raw: str, normalized: str) -> None:
    assert validate_endpoint(raw) == normalized


@pytest.mark.parametrize(
    "raw",
    [
        "http://remote.test:4096",
        "file:///tmp/server",
        "https://user:password@example.test",
        "https://example.test?token=secret",
    ],
)
def test_endpoint_policy_refuses_unsafe_forms(raw: str) -> None:
    with pytest.raises(OpenCodeAuthError):
        validate_endpoint(raw)


def test_remote_cleartext_requires_explicit_operator_override() -> None:
    assert validate_endpoint("http://remote.test:4096", allow_insecure_http=True) == (
        "http://remote.test:4096"
    )


def test_password_file_keeps_crlf_and_a_ctrl_z_byte(tmp_path: Path) -> None:
    """A CRLF pair and a Ctrl-Z byte stay in the password as stored."""
    path = tmp_path / "password"
    path.write_bytes(b"se\r\ncret\x1atail")
    os.chmod(path, 0o600)
    assert load_password_file(path) == "se\r\ncret\x1atail"


def test_password_file_is_owner_only_bounded_and_newline_tolerant(tmp_path: Path) -> None:
    path = tmp_path / "password"
    path.write_text("correct horse\n")
    if os.name == "nt":
        apply_owner_only_file(path)
        assert load_password_file(path) == "correct horse"
        subprocess.run(
            ["icacls", os.fspath(path), "/grant", "*S-1-1-0:(R)"],
            check=True,
            capture_output=True,
        )
        refused = "user-owned regular file"
    else:
        os.chmod(path, 0o600)
        assert load_password_file(path) == "correct horse"
        os.chmod(path, 0o640)
        refused = "group or others"
    with pytest.raises(OpenCodeAuthError, match=refused):
        load_password_file(path)


def _owner_only(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    if os.name == "nt":
        apply_owner_only_file(path)
    else:
        os.chmod(path, 0o600)


def test_password_file_replaced_while_it_is_opened_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file whose owner and mode were checked must be the file that is read."""
    path = tmp_path / "password"
    replacement = tmp_path / "replacement"
    _owner_only(path, b"checked")
    _owner_only(replacement, b"swapped")
    real_open = os.open
    swapped: list[str] = []

    def swap_then_open(target: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        """Replace the checked file in the window between its checks and its open.

        The reader opens the file once; a second open would find no replacement
        left to move and fail the test loudly.
        """
        swapped.append(os.fspath(target))
        os.replace(replacement, path)
        return real_open(target, flags, mode)

    monkeypatch.setattr(os, "open", swap_then_open)
    with pytest.raises(OpenCodeAuthError, match="changed while opening"):
        load_password_file(path)
    assert swapped == [os.fspath(path)]
    assert path.read_bytes() == b"swapped"


def test_password_file_on_a_host_neither_posix_nor_windows_is_refused_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host with no POSIX owner check and no Windows access check cannot prove owner-only."""
    path = tmp_path / "password"
    _owner_only(path, b"never read")
    real_open = os.open
    opened: list[str] = []

    def recording_open(target: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        opened.append(os.fspath(target))
        return real_open(target, flags, mode)

    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.setattr(os, "name", "unsupported")
    monkeypatch.setattr(os, "open", recording_open)
    with pytest.raises(OpenCodeAuthError, match=r"^OpenCode password file must be a user-owned"):
        load_password_file(path)
    assert opened == []


def test_password_file_refuses_symlink_and_empty_secret(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("\n")
    os.chmod(target, 0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OpenCodeAuthError, match="regular file"):
        load_password_file(link)
    with pytest.raises(OpenCodeAuthError, match="empty"):
        load_password_file(target)


def test_password_file_refuses_oversized_non_utf8_and_nul(tmp_path: Path) -> None:
    path = tmp_path / "password"
    path.write_bytes(b"x" * 8_193)
    os.chmod(path, 0o600)
    with pytest.raises(OpenCodeAuthError, match="bounded size"):
        load_password_file(path)
    path.write_bytes(b"\xff")
    with pytest.raises(OpenCodeAuthError, match="UTF-8"):
        load_password_file(path)
    path.write_bytes(b"bad\0secret")
    with pytest.raises(OpenCodeAuthError, match="NUL"):
        load_password_file(path)


def test_basic_header_is_constructed_without_mutating_secret() -> None:
    value = basic_authorization("opencode", "páss")
    assert value.startswith("Basic ")
    assert base64.b64decode(value.removeprefix("Basic ")).decode() == "opencode:páss"
    with pytest.raises(OpenCodeAuthError):
        basic_authorization("bad:user", "secret")

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import base64
import os
from pathlib import Path

import pytest

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


def test_password_file_is_owner_only_bounded_and_newline_tolerant(tmp_path: Path) -> None:
    path = tmp_path / "password"
    path.write_text("correct horse\n")
    os.chmod(path, 0o600)
    assert load_password_file(path) == "correct horse"
    os.chmod(path, 0o640)
    with pytest.raises(OpenCodeAuthError, match="group or others"):
        load_password_file(path)


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

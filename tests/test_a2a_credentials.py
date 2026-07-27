# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A bearer custody and plaintext transport tests
"""Exercise A2A token-file precedence and fail-closed cleartext policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from _platform_caps import requires_owner_only_secrets, requires_posix_mode_bits
from synapse_channel import cli
from synapse_channel.a2a_credentials import (
    A2APlaintextBearerError,
    guard_a2a_bearer_transport,
    is_literal_loopback_host,
    resolve_a2a_token,
)
from synapse_channel.core.secret_files import SecretFileError


def test_no_a2a_credential_source_resolves_none() -> None:
    assert resolve_a2a_token(None, None) is None


def test_explicit_a2a_token_wins_without_opening_file(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-open"
    assert resolve_a2a_token("argv-bearer", missing) == "argv-bearer"
    assert not missing.exists()


@requires_owner_only_secrets
def test_a2a_token_file_uses_shared_owner_only_loader(tmp_path: Path) -> None:
    token_file = tmp_path / "a2a.token"
    token_file.write_text("  file-bearer\n", encoding="utf-8")
    token_file.chmod(0o600)

    assert resolve_a2a_token(None, token_file) == "file-bearer"


@requires_posix_mode_bits
def test_a2a_token_file_error_never_contains_secret(tmp_path: Path) -> None:
    token_file = tmp_path / "a2a.token"
    token_file.write_text("never-print-this-a2a-secret\n", encoding="utf-8")
    token_file.chmod(0o644)

    with pytest.raises(SecretFileError) as caught:
        resolve_a2a_token(None, token_file)

    message = str(caught.value)
    assert "--a2a-token-file" in message
    assert "never-print-this-a2a-secret" not in message


@requires_posix_mode_bits
@pytest.mark.parametrize(
    "arguments",
    (
        ("a2a-serve", "--endpoint-url", "https://peer.example", "--bearer-auth"),
        ("a2a-client", "--endpoint-url", "https://peer.example"),
        ("a2a-interop-trace", "--scheme", "https"),
    ),
)
def test_every_a2a_cli_redacts_rejected_token_file(
    arguments: tuple[str, ...],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token_file = tmp_path / "a2a.token"
    token_file.write_text("never-print-this-cli-secret\n", encoding="utf-8")
    token_file.chmod(0o644)

    assert cli.main([*arguments, "--a2a-token-file", str(token_file)]) == 2
    error = capsys.readouterr().err
    assert "--a2a-token-file" in error
    assert "never-print-this-cli-secret" not in error


@pytest.mark.parametrize("host", ("127.0.0.1", "127.255.255.254", "::1"))
def test_literal_loopback_http_allows_bearer(host: str) -> None:
    assert is_literal_loopback_host(host)
    guard_a2a_bearer_transport(scheme="http", host=host, token="secret")


@pytest.mark.parametrize(
    "host",
    ("localhost", "example.test", "10.0.0.4", "", "0.0.0.0", " 127.0.0.1 "),
)
def test_remote_or_named_plaintext_http_refuses_bearer(host: str) -> None:
    assert not is_literal_loopback_host(host)
    with pytest.raises(A2APlaintextBearerError, match="plaintext HTTP"):
        guard_a2a_bearer_transport(scheme="http", host=host, token="never echoed")


def test_plaintext_guard_never_includes_bearer_value() -> None:
    with pytest.raises(A2APlaintextBearerError) as caught:
        guard_a2a_bearer_transport(
            scheme="http",
            host="peer.example",
            token="never-print-this-bearer",
        )
    assert "never-print-this-bearer" not in str(caught.value)


def test_https_tokenless_and_explicit_override_are_allowed() -> None:
    guard_a2a_bearer_transport(scheme="https", host="peer.example", token="secret")
    guard_a2a_bearer_transport(scheme="http", host="peer.example", token=None)
    guard_a2a_bearer_transport(
        scheme="http",
        host="peer.example",
        token="secret",
        allow_insecure_http=True,
    )

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual listener handoff and authority role validation
"""Verify real process credentials and socket states without fabricating root custody."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

from protected_write_socket_examples import private_unix_directory as private_unix_directory
from synapse_channel.core.protected_write_listener import (
    validate_authority_role_policy,
    validate_protected_authority_listener,
)


def native_credentials() -> bool:
    """Report the actual platform capability; unsupported hosts must refuse the public entry."""
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        with pytest.raises(OSError, match="require Linux SO_PEERCRED"):
            validate_authority_role_policy(1, forbidden_uids=frozenset({0}))
        return False
    return True


def operator_uid() -> int:
    """Choose an existing distinct account for policy metadata, never process impersonation."""
    import pwd

    return next(
        record.pw_uid for record in pwd.getpwall() if record.pw_uid not in {0, os.geteuid()}
    )


def test_configurable_exclusions_observe_the_real_authority_uid() -> None:
    """An explicit policy can allow this actual non-root process without root custody claims."""
    if not native_credentials():
        return
    assert validate_authority_role_policy(operator_uid(), forbidden_uids=frozenset({0})) == (
        os.geteuid()
    )
    with pytest.raises(ValueError, match="separate dedicated UIDs"):
        validate_authority_role_policy(operator_uid(), forbidden_uids=frozenset({0, os.geteuid()}))


@pytest.mark.parametrize("forbidden", [frozenset(), frozenset({1}), frozenset({0, -1})])
def test_forbidden_policy_keeps_root_excluded(forbidden: frozenset[int]) -> None:
    """Invalid actual policy inputs cannot remove the non-root authority prerequisite."""
    if not native_credentials():
        return
    with pytest.raises(ValueError, match="invalid protected authority forbidden UID policy"):
        validate_authority_role_policy(operator_uid(), forbidden_uids=forbidden)


@pytest.mark.parametrize("uid", [0, -1, True])
def test_operator_role_refuses_forbidden_or_invalid_identity(uid: int) -> None:
    """Reject malformed or forbidden operator metadata while retaining actual caller credentials."""
    if not native_credentials():
        return
    with pytest.raises(ValueError, match="separate dedicated UIDs"):
        validate_authority_role_policy(uid, forbidden_uids=frozenset({0}))
    with pytest.raises(ValueError, match="separate dedicated UIDs"):
        validate_authority_role_policy(os.geteuid(), forbidden_uids=frozenset({0}))


def test_real_tcp_socket_cannot_be_an_authority_listener() -> None:
    """The public validator rejects the actual observed socket family."""
    if not native_credentials():
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        with pytest.raises(ValueError, match="operator listener must be a Unix stream socket"):
            validate_protected_authority_listener(
                listener, operator_uid(), forbidden_uids=frozenset({0})
            )


def test_real_unbound_stream_and_datagram_are_refused() -> None:
    """Actual Unix sockets must have both a stream type and an absolute bound path."""
    if not native_credentials():
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
        with pytest.raises(ValueError, match="request listener must be a Unix stream socket"):
            validate_protected_authority_listener(
                listener, operator_uid(), forbidden_uids=frozenset({0}), endpoint="request"
            )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        with pytest.raises(
            ValueError, match="request listener requires an absolute filesystem path"
        ):
            validate_protected_authority_listener(
                listener, operator_uid(), forbidden_uids=frozenset({0}), endpoint="request"
            )
        with pytest.raises(ValueError, match="listening under the authority UID"):
            validate_protected_authority_listener(
                listener, operator_uid(), forbidden_uids=frozenset({0}), listening=True
            )


def test_real_listen_handoff_and_node_owner_are_independent_guards(
    private_unix_directory: Path,
) -> None:
    """Refuse a real caller-owned node and a premature listen without faking root metadata."""
    if not native_credentials():
        return
    path = private_unix_directory / "request.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        path.chmod(0o600)
        with pytest.raises(ValueError, match="request listener must be root-owned"):
            validate_protected_authority_listener(
                listener, operator_uid(), forbidden_uids=frozenset({0}), endpoint="request"
            )
        listener.listen(1)
        with pytest.raises(ValueError, match="request listener must be bound before listen"):
            validate_protected_authority_listener(
                listener, operator_uid(), forbidden_uids=frozenset({0}), endpoint="request"
            )
        with pytest.raises(ValueError, match="request listener must be root-owned"):
            validate_protected_authority_listener(
                listener,
                operator_uid(),
                forbidden_uids=frozenset({0}),
                endpoint="request",
                listening=True,
            )

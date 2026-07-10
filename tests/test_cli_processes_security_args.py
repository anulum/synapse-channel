# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub security argument registration tests
"""Exercise the extracted security argument registrar as one production surface."""

from __future__ import annotations

import argparse

import pytest

from synapse_channel.cli_processes_security_args import add_hub_security_arguments


def _security_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_hub_security_arguments(parser)
    return parser


def test_security_argument_defaults_preserve_local_first_posture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Extracting registration leaves every opt-in security gate disabled."""
    args = _security_parser().parse_args([])

    assert args.token is None
    assert args.metrics is False
    assert args.metrics_query_token_ok is False
    assert args.message_auth_key == []
    assert args.require_message_auth is False
    assert args.require_acl is False
    assert args.require_role_claim is False
    assert args.require_identity_binding is False
    assert args.private_directed_messages is False
    assert args.paranoid is False
    assert args.team_secure is False
    assert capsys.readouterr().err == ""


def test_security_argument_registrar_accepts_complete_explicit_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The extracted registrar preserves values for every security layer."""
    args = _security_parser().parse_args(
        [
            "--tls-certfile",
            "cert.pem",
            "--tls-keyfile",
            "key.pem",
            "--paranoid",
            "--team-secure",
            "--token",
            "fixture-token",
            "--metrics",
            "--metrics-token",
            "fixture-metrics",
            "--metrics-query-token-ok",
            "--message-auth-key",
            "key-id:fixture-hmac:ALPHA",
            "--require-message-auth",
            "--acl-policy",
            "acl.json",
            "--require-acl",
            "--role-grants",
            "roles.json",
            "--require-role-claim",
            "--identity-trust",
            "trust.json",
            "--require-identity-binding",
            "--private-directed-messages",
        ]
    )

    assert args.tls_certfile == "cert.pem"
    assert args.tls_keyfile == "key.pem"
    assert args.paranoid is True
    assert args.team_secure is True
    assert args.token == "fixture-token"
    assert args.metrics is True
    assert args.metrics_token == "fixture-metrics"
    assert args.metrics_query_token_ok is True
    assert args.message_auth_key == ["key-id:fixture-hmac:ALPHA"]
    assert args.require_message_auth is True
    assert args.acl_policy == "acl.json"
    assert args.require_acl is True
    assert args.role_grants == "roles.json"
    assert args.require_role_claim is True
    assert args.identity_trust == "trust.json"
    assert args.require_identity_binding is True
    assert args.private_directed_messages is True
    assert "--metrics-query-token-ok is deprecated" in capsys.readouterr().err

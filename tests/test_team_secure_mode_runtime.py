# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — team-secure hub runtime tests

from __future__ import annotations

import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from synapse_channel import cli, cli_processes
from synapse_channel.core.hub import SynapseHub


def _close_runner(coro: Coroutine[Any, Any, None]) -> None:
    coro.close()


def _write_identity_trust(path: Path) -> None:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    )
    path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "key_id": "k",
                        "public_key": base64.b64encode(raw).decode("ascii"),
                        "senders": ["proj/claude"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _write_role_grants(path: Path) -> None:
    path.write_text(
        json.dumps({"grants": {"proj/coordinator": ["proj/claude"]}}),
        encoding="utf-8",
    )


def test_parser_hub_team_secure_switch_defaults_to_off() -> None:
    """The hub parser exposes an explicit team-secure runtime switch."""
    defaults = cli.build_parser().parse_args(["hub"])
    enabled = cli.build_parser().parse_args(["hub", "--team-secure"])

    assert defaults.team_secure is False
    assert enabled.team_secure is True


def test_cmd_hub_team_secure_refuses_missing_token(capsys: pytest.CaptureFixture[str]) -> None:
    """Team-secure hub startup fails closed before building an unauthenticated hub."""
    assert (
        cli_processes._cmd_hub(
            _hub_ns(team_secure=True, identity_trust="t.json", role_grants="r.json"),
            runner=_close_runner,
        )
        == 2
    )
    assert "team-secure mode requires --token" in capsys.readouterr().err


def test_cmd_hub_team_secure_refuses_missing_identity_trust(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Team-secure hub startup requires the identity trust bundle path."""
    assert (
        cli_processes._cmd_hub(
            _hub_ns(team_secure=True, token="s3cret", role_grants="r.json"),
            runner=_close_runner,
        )
        == 2
    )
    assert "team-secure mode requires --identity-trust" in capsys.readouterr().err


def test_cmd_hub_team_secure_refuses_missing_role_grants(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Team-secure hub startup requires the role-grant store path."""
    assert (
        cli_processes._cmd_hub(
            _hub_ns(team_secure=True, token="s3cret", identity_trust="t.json"),
            runner=_close_runner,
        )
        == 2
    )
    assert "team-secure mode requires --role-grants" in capsys.readouterr().err


def test_cmd_hub_team_secure_forces_trust_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A complete team-secure profile enables identity, roles, and private directed chat."""
    trust = tmp_path / "identity-trust.json"
    grants = tmp_path / "role-grants.json"
    _write_identity_trust(trust)
    _write_role_grants(grants)
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                team_secure=True,
                token="s3cret",
                identity_trust=str(trust),
                role_grants=str(grants),
                # Individual gates left off — the profile must force them on.
                require_identity_binding=False,
                require_role_claim=False,
                private_directed_messages=False,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["require_identity_binding"] is True
    assert captured["require_role_claim"] is True
    assert captured["private_directed_messages"] is True
    assert "k" in captured["identity_trust_bundle"].keys
    assert captured["role_grants"].may_claim("proj/claude", "proj/coordinator")
    err = capsys.readouterr().err
    assert "team-secure mode enforced:" in err
    assert "team-secure mode recommended next:" in err

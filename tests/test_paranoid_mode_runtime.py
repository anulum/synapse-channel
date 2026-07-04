# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — paranoid hub runtime tests

from __future__ import annotations

import ssl
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from synapse_channel import cli, cli_processes
from synapse_channel.core.hub import SynapseHub


def _write_acl_policy(tmp_path: Path) -> Path:
    """Write a minimal valid one-rule ACL policy for paranoid startup."""
    policy = tmp_path / "acl.json"
    policy.write_text(
        '{"rules": [{"permission": "claim", "target_kind": "path", "target_pattern": "src/*"}]}',
        encoding="utf-8",
    )
    return policy


def _close_runner(coro: Coroutine[Any, Any, None]) -> None:
    """Close a hub coroutine without running a long-lived server."""
    coro.close()


def test_parser_hub_paranoid_switch_defaults_to_off() -> None:
    """The hub parser exposes an explicit paranoid runtime switch."""
    defaults = cli.build_parser().parse_args(["hub"])
    enabled = cli.build_parser().parse_args(["hub", "--paranoid"])

    assert defaults.paranoid is False
    assert enabled.paranoid is True


def test_cmd_hub_paranoid_refuses_missing_token(capsys: pytest.CaptureFixture[str]) -> None:
    """Paranoid hub startup fails closed before building an unauthenticated hub."""
    assert cli_processes._cmd_hub(_hub_ns(paranoid=True, db="hub.db"), runner=_close_runner) == 2

    assert "paranoid mode requires --token or --token-file" in capsys.readouterr().err


def test_cmd_hub_paranoid_refuses_missing_durable_event_log(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Paranoid hub startup requires durable replay evidence."""
    assert cli_processes._cmd_hub(_hub_ns(paranoid=True, token="s3cret"), runner=_close_runner) == 2

    assert "paranoid mode requires --db" in capsys.readouterr().err


def test_cmd_hub_paranoid_refuses_metrics_without_metrics_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Metrics stay auth-gated when paranoid mode enables a hub."""
    args = _hub_ns(paranoid=True, token="s3cret", db="hub.db", metrics=True)

    assert cli_processes._cmd_hub(args, runner=_close_runner) == 2

    assert "paranoid mode requires --metrics-token when --metrics is enabled" in (
        capsys.readouterr().err
    )


def test_cmd_hub_paranoid_refuses_without_acl_enforcement(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Paranoid mode requires ACL enforcement with a policy."""
    args = _hub_ns(
        paranoid=True,
        token="s3cret",
        db="hub.db",
        message_auth_key=["main:shared-secret:ALPHA"],
        require_message_auth=True,
    )
    assert cli_processes._cmd_hub(args, runner=_close_runner) == 2
    assert "paranoid mode requires --require-acl" in capsys.readouterr().err


def test_cmd_hub_paranoid_refuses_without_native_wss(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Paranoid mode requires native WSS certificates."""
    args = _hub_ns(
        paranoid=True,
        token="s3cret",
        db="hub.db",
        message_auth_key=["main:shared-secret:ALPHA"],
        require_message_auth=True,
        require_acl=True,
        acl_policy=str(_write_acl_policy(tmp_path)),
    )
    assert cli_processes._cmd_hub(args, runner=_close_runner) == 2
    assert "paranoid mode requires native WSS" in capsys.readouterr().err


def test_cmd_hub_paranoid_applies_strict_runtime_settings(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Paranoid mode disables relaxed settings and reports future missing hooks."""
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    def fake_tls(**_kwargs: Any) -> ssl.SSLContext | None:
        # The paranoid gate only requires the cert/key paths to be present; the real
        # TLS context build is exercised by the TLS tests, so it is stubbed here.
        return None

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                paranoid=True,
                token="s3cret",
                db="hub.db",
                metrics=True,
                metrics_token="metrics",
                message_auth_key=["main:shared-secret:ALPHA"],
                require_message_auth=True,
                require_acl=True,
                acl_policy=str(_write_acl_policy(tmp_path)),
                tls_certfile="cert.pem",
                tls_keyfile="key.pem",
                metrics_query_token_ok=True,
                insecure_off_loopback=True,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
            tls_context_factory=fake_tls,
        )
        == 0
    )

    assert captured["authenticator"] is not None
    assert captured["metrics_token"] == "metrics"
    assert captured["metrics_query_token_ok"] is False
    assert captured["require_per_message_auth"] is True
    assert captured["require_acl"] is True
    assert captured["insecure_off_loopback"] is False
    assert "paranoid mode missing hooks:" in capsys.readouterr().err

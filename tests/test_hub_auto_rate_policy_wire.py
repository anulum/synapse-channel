# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-startup wire for rate-policy auto-enable (REV-SEC-06)

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from synapse_channel.cli_processes_hub import _apply_auto_rate_policy, _cmd_hub
from synapse_channel.core.secure import (
    SECURE_AGENT_BURST,
    SECURE_AGENT_RATE,
    SECURE_HOST_BURST,
    SECURE_HOST_RATE,
    SECURE_MAX_CONNECTIONS_PER_HOST,
)


def _close_runner(coro: Coroutine[Any, Any, None]) -> None:
    coro.close()


def test_apply_auto_rate_policy_loopback_single_seat_unchanged() -> None:
    args = _hub_ns(host="127.0.0.1", rate=0.0, burst=0.0, host_rate=0.0, host_burst=0.0)
    args.max_connections_per_host = 0
    args.token = None
    args.token_file = None
    args.secure = False
    args.team_secure = False
    _apply_auto_rate_policy(args)
    assert args.rate == 0.0
    assert args.host_rate == 0.0
    assert args.max_connections_per_host == 0


def test_apply_auto_rate_policy_token_on_loopback_fills_disabled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _hub_ns(host="127.0.0.1", rate=0.0, burst=0.0, host_rate=0.0, host_burst=0.0)
    args.max_connections_per_host = 0
    args.token = "secret-token"
    args.token_file = None
    args.secure = False
    args.team_secure = False
    _apply_auto_rate_policy(args)
    assert args.rate == SECURE_AGENT_RATE
    assert args.burst == SECURE_AGENT_BURST
    assert args.host_rate == SECURE_HOST_RATE
    assert args.host_burst == SECURE_HOST_BURST
    assert args.max_connections_per_host == SECURE_MAX_CONNECTIONS_PER_HOST
    err = capsys.readouterr().err
    assert "flood auto-enable" in err
    assert "connect token" in err


def test_apply_auto_rate_policy_off_loopback_fills_disabled() -> None:
    args = _hub_ns(host="0.0.0.0", rate=0.0, burst=0.0, host_rate=0.0, host_burst=0.0)
    args.max_connections_per_host = 0
    args.token = None
    args.token_file = None
    args.secure = False
    args.team_secure = False
    args.insecure_off_loopback = True  # bind path may still refuse; policy only
    _apply_auto_rate_policy(args)
    assert args.rate == SECURE_AGENT_RATE
    assert args.max_connections_per_host == SECURE_MAX_CONNECTIONS_PER_HOST


def test_apply_auto_rate_policy_preserves_operator_positive_limits() -> None:
    args = _hub_ns(host="0.0.0.0", rate=7.0, burst=3.0, host_rate=11.0, host_burst=5.0)
    args.max_connections_per_host = 2
    args.token = "t"
    args.secure = False
    _apply_auto_rate_policy(args)
    assert args.rate == 7.0
    assert args.burst == 3.0
    assert args.host_rate == 11.0
    assert args.host_burst == 5.0
    assert args.max_connections_per_host == 2


def test_apply_auto_rate_policy_secure_mode_stands_down() -> None:
    args = _hub_ns(host="0.0.0.0", rate=0.0, burst=0.0, host_rate=0.0, host_burst=0.0)
    args.max_connections_per_host = 0
    args.token = "t"
    args.secure = True
    _apply_auto_rate_policy(args)
    assert args.rate == 0.0
    assert args.max_connections_per_host == 0


def test_cmd_hub_wires_auto_rate_before_limiters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end hub startup applies auto limits onto the RateLimiter path."""
    captured: dict[str, Any] = {}

    def hub_factory(**kwargs: Any) -> Any:
        captured["kwargs"] = kwargs

        class _FakeHub:
            def serve(self, **_serve_kwargs: Any) -> Coroutine[Any, Any, None]:
                async def _never() -> None:
                    return None

                return _never()

        return _FakeHub()

    ns = _hub_ns(host="127.0.0.1", rate=0.0, burst=0.0, host_rate=0.0, host_burst=0.0)
    ns.max_connections_per_host = 0
    ns.token = "wire-token"
    ns.secure = False
    assert _cmd_hub(ns, runner=_close_runner, hub_factory=hub_factory) == 0
    assert ns.rate == SECURE_AGENT_RATE
    rate_limiter = captured["kwargs"]["rate_limiter"]
    assert rate_limiter is not None
    assert rate_limiter.rate_per_second == SECURE_AGENT_RATE
    host_limiter = captured["kwargs"]["host_rate_limiter"]
    assert host_limiter is not None
    assert host_limiter.rate_per_second == SECURE_HOST_RATE
    assert captured["kwargs"]["max_connections_per_host"] == SECURE_MAX_CONNECTIONS_PER_HOST
    err = capsys.readouterr().err
    assert "flood auto-enable" in err


def test_apply_auto_rate_policy_expect_multi_seat_on_loopback_fills() -> None:
    args = _hub_ns(host="127.0.0.1", rate=0.0, burst=0.0, host_rate=0.0, host_burst=0.0)
    args.max_connections_per_host = 0
    args.token = None
    args.token_file = None
    args.secure = False
    args.team_secure = False
    args.expect_multi_seat = True
    args.bridge_exposed = False
    _apply_auto_rate_policy(args)
    assert args.rate == SECURE_AGENT_RATE
    assert args.max_connections_per_host == SECURE_MAX_CONNECTIONS_PER_HOST


def test_apply_auto_rate_policy_identity_trust_implies_multi_seat() -> None:
    args = _hub_ns(host="127.0.0.1", rate=0.0, burst=0.0, host_rate=0.0, host_burst=0.0)
    args.max_connections_per_host = 0
    args.token = None
    args.secure = False
    args.team_secure = False
    args.identity_trust = "/tmp/trust.json"
    args.expect_multi_seat = False
    args.bridge_exposed = False
    _apply_auto_rate_policy(args)
    assert args.rate == SECURE_AGENT_RATE


def test_apply_auto_rate_policy_bridge_exposed_on_loopback_fills() -> None:
    args = _hub_ns(host="127.0.0.1", rate=0.0, burst=0.0, host_rate=0.0, host_burst=0.0)
    args.max_connections_per_host = 0
    args.token = None
    args.secure = False
    args.team_secure = False
    args.expect_multi_seat = False
    args.bridge_exposed = True
    _apply_auto_rate_policy(args)
    assert args.rate == SECURE_AGENT_RATE
    assert args.max_connections_per_host == SECURE_MAX_CONNECTIONS_PER_HOST


def test_apply_auto_rate_policy_private_directed_implies_multi_seat() -> None:
    args = _hub_ns(host="127.0.0.1", rate=0.0, burst=0.0, host_rate=0.0, host_burst=0.0)
    args.max_connections_per_host = 0
    args.token = None
    args.secure = False
    args.private_directed_messages = True
    args.bridge_exposed = False
    args.expect_multi_seat = False
    _apply_auto_rate_policy(args)
    assert args.rate == SECURE_AGENT_RATE

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the plaintext-token off-loopback bind refusal

"""A shared token over plaintext ``ws://`` off loopback is refused by default.

From the 1.0 posture a token presented over plaintext ``ws://`` off loopback is a
refusal, not a mere advisory: the token and every coordination frame would be
readable on the network path. These tests pin that exact posture — the problem
fires only for authenticator + off-loopback + no TLS, the guard raises
:class:`InsecureBindError` by default, ``--insecure-off-loopback`` downgrades it to
a single warning, native TLS clears it entirely, the plaintext problem rides
alongside other refusals in one message, and the real ``serve()`` bind threads its
``ssl_context`` into the decision so an unencrypted off-loopback bind never starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket

import pytest

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_exposure import (
    InsecureBindError,
    guard_exposure,
    plaintext_token_problems,
)

logger = logging.getLogger("synapse.hub")


def _problems(host: str, *, authenticator: object | None, tls_active: bool) -> list[str]:
    return plaintext_token_problems(host, authenticator=authenticator, tls_active=tls_active)


def test_plaintext_token_problems_empty_on_loopback() -> None:
    token = TokenAuthenticator(["s3cret"])
    assert _problems("localhost", authenticator=token, tls_active=False) == []
    assert _problems("127.0.0.1", authenticator=token, tls_active=False) == []
    assert _problems("::1", authenticator=token, tls_active=False) == []


def test_plaintext_token_problems_empty_without_authenticator() -> None:
    # No token off loopback is the separate no-token refusal (exposure_problems),
    # not the plaintext-token lane.
    assert _problems("0.0.0.0", authenticator=None, tls_active=False) == []


def test_plaintext_token_problems_empty_when_tls_terminates_the_bind() -> None:
    token = TokenAuthenticator(["s3cret"])
    assert _problems("0.0.0.0", authenticator=token, tls_active=True) == []


def test_plaintext_token_problem_names_the_risk_and_both_remedies() -> None:
    token = TokenAuthenticator(["s3cret"])
    problems = _problems("192.168.1.20", authenticator=token, tls_active=False)
    assert len(problems) == 1
    problem = problems[0]
    assert "'192.168.1.20'" in problem
    assert "plaintext ws://" in problem
    assert "--tls-certfile" in problem
    assert "wss:// proxy" in problem


def test_guard_refuses_token_over_plaintext_off_loopback() -> None:
    with pytest.raises(InsecureBindError, match="plaintext ws://") as exc_info:
        guard_exposure(
            "0.0.0.0",
            authenticator=TokenAuthenticator(["s3cret"]),
            enable_metrics=False,
            metrics_token=None,
            insecure_off_loopback=False,
            tls_active=False,
            logger=logger,
        )
    message = str(exc_info.value)
    assert "Refusing to bind" in message
    assert "--insecure-off-loopback" in message


def test_guard_downgrades_plaintext_token_to_single_warning_when_overridden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="synapse.hub"):
        guard_exposure(
            "0.0.0.0",
            authenticator=TokenAuthenticator(["s3cret"]),
            enable_metrics=False,
            metrics_token=None,
            insecure_off_loopback=True,
            tls_active=False,
            logger=logger,
        )
    warnings = [r for r in caplog.records if "plaintext ws://" in r.getMessage()]
    assert len(warnings) == 1


def test_guard_combines_plaintext_token_with_metrics_refusal() -> None:
    # A token satisfies the connect guard, but metrics without a metrics token
    # still refuses; the plaintext-token problem must ride in the same refusal so
    # the operator sees both before fixing and restarting.
    with pytest.raises(InsecureBindError) as exc_info:
        guard_exposure(
            "0.0.0.0",
            authenticator=TokenAuthenticator(["s3cret"]),
            enable_metrics=True,
            metrics_token=None,
            insecure_off_loopback=False,
            tls_active=False,
            logger=logger,
        )
    message = str(exc_info.value)
    assert "metrics" in message
    assert "plaintext ws://" in message


def test_guard_stays_silent_with_token_behind_tls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="synapse.hub"):
        guard_exposure(
            "0.0.0.0",
            authenticator=TokenAuthenticator(["s3cret"]),
            enable_metrics=False,
            metrics_token=None,
            insecure_off_loopback=False,
            tls_active=True,
            logger=logger,
        )
    assert caplog.records == []


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def _serve_briefly(hub: SynapseHub, host: str, port: int) -> None:
    task = asyncio.create_task(hub.serve(host, port))
    try:
        for _ in range(200):
            await asyncio.sleep(0.01)
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    return
        raise AssertionError(f"hub never started listening on port {port}")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_serve_refuses_plaintext_token_bind_off_loopback() -> None:
    hub = SynapseHub(
        hub_id="syn-plaintext",
        authenticator=TokenAuthenticator(["s3cret"]),
    )
    with pytest.raises(InsecureBindError, match="plaintext ws://"):
        await hub.serve("0.0.0.0", _free_port())


async def test_serve_keeps_loopback_bind_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SynapseHub(
        hub_id="syn-plaintext",
        authenticator=TokenAuthenticator(["s3cret"]),
    )
    with caplog.at_level("WARNING", logger="synapse.hub"):
        await _serve_briefly(hub, "localhost", _free_port())
    assert caplog.records == []

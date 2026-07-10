# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the plaintext-transport exposure advisory

"""The token-over-plaintext advisory warns without ever blocking a bind.

SECURITY.md marks transport encryption *recommended* (not required) on a team
LAN, so a token presented over ``ws://`` off loopback must keep starting — but
never silently: the shared token and every frame are readable on the network
path. These tests pin the advisory to that exact posture: it fires only for
authenticator + off-loopback + no TLS, it rides along both the refusal and the
override paths of the guard, and the real ``serve()`` bind threads its
``ssl_context`` into the decision.
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
    exposure_advisories,
    guard_exposure,
)

logger = logging.getLogger("synapse.hub")


def _advisories(host: str, *, authenticator: object | None, tls_active: bool) -> list[str]:
    return exposure_advisories(host, authenticator=authenticator, tls_active=tls_active)


def test_advisories_empty_on_loopback() -> None:
    token = TokenAuthenticator(["s3cret"])
    assert _advisories("localhost", authenticator=token, tls_active=False) == []
    assert _advisories("127.0.0.1", authenticator=token, tls_active=False) == []
    assert _advisories("::1", authenticator=token, tls_active=False) == []


def test_advisories_empty_without_authenticator() -> None:
    # No token off loopback is the REFUSAL lane (exposure_problems), not an advisory.
    assert _advisories("0.0.0.0", authenticator=None, tls_active=False) == []


def test_advisories_empty_when_tls_terminates_the_bind() -> None:
    token = TokenAuthenticator(["s3cret"])
    assert _advisories("0.0.0.0", authenticator=token, tls_active=True) == []


def test_advisory_names_the_risk_and_both_remedies() -> None:
    token = TokenAuthenticator(["s3cret"])
    advisories = _advisories("192.168.1.20", authenticator=token, tls_active=False)
    assert len(advisories) == 1
    advisory = advisories[0]
    assert "'192.168.1.20'" in advisory
    assert "plaintext ws://" in advisory
    assert "--tls-certfile" in advisory
    assert "--paranoid" in advisory


def test_guard_logs_the_advisory_even_when_it_refuses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A token satisfies the connect guard, but metrics without a metrics token
    # still refuses — the advisory must already be on record for the operator
    # who then fixes only the refusal and starts again over plaintext.
    with caplog.at_level("WARNING", logger="synapse.hub"):
        with pytest.raises(InsecureBindError, match="metrics"):
            guard_exposure(
                "0.0.0.0",
                authenticator=TokenAuthenticator(["s3cret"]),
                enable_metrics=True,
                metrics_token=None,
                insecure_off_loopback=False,
                tls_active=False,
                logger=logger,
            )
    assert "plaintext ws://" in caplog.text


def test_guard_logs_the_advisory_once_on_the_override_path(
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
    advisories = [r for r in caplog.records if "plaintext ws://" in r.getMessage()]
    assert len(advisories) == 1


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


async def test_serve_threads_plaintext_bind_into_the_advisory(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SynapseHub(
        hub_id="syn-advisory",
        authenticator=TokenAuthenticator(["s3cret"]),
    )
    with caplog.at_level("WARNING", logger="synapse.hub"):
        await _serve_briefly(hub, "0.0.0.0", _free_port())
    assert "plaintext ws://" in caplog.text


async def test_serve_keeps_loopback_bind_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SynapseHub(
        hub_id="syn-advisory",
        authenticator=TokenAuthenticator(["s3cret"]),
    )
    with caplog.at_level("WARNING", logger="synapse.hub"):
        await _serve_briefly(hub, "localhost", _free_port())
    assert caplog.records == []

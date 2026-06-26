# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the messaging CLI commands (send/wait/listen)

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_messaging
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub

REPO_ROOT = Path(__file__).resolve().parents[1]


async def test_send_delivers_message_and_prints_replies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        fast = await connect_agent("FAST", uri)
        send_task = asyncio.create_task(
            cli_messaging._send(
                uri=uri,
                name="USER",
                target="FAST",
                message="ping",
                wait_seconds=0.2,
            )
        )
        try:
            await fast.recorder.wait_for(
                lambda message: (
                    message.get("type") == "chat"
                    and message.get("sender") == "USER"
                    and message.get("payload") == "ping"
                )
            )
            await fast.agent.chat("pong", target="USER")
            code = await send_task
        finally:
            await close_agents(fast)

    assert code == 0
    out = capsys.readouterr().out
    assert "FAST: pong" in out


async def test_send_waits_but_prints_nothing_without_replies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        code = await cli_messaging._send(
            uri=uri,
            name="USER",
            target="all",
            message="ping",
            wait_seconds=0.01,
        )

    assert code == 0
    assert capsys.readouterr().out == ""


async def test_send_skips_wait_when_zero() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        try:
            code = await cli_messaging._send(
                uri=uri,
                name="USER",
                target="all",
                message="ping",
                wait_seconds=0.0,
            )
            message = await observer.recorder.wait_for(
                lambda item: item.get("type") == "chat" and item.get("sender") == "USER"
            )
        finally:
            await close_agents(observer)

    assert code == 0
    assert message["target"] == "all"
    assert message["payload"] == "ping"


async def test_send_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_messaging._send(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="USER",
        target="all",
        message="ping",
        wait_seconds=0.0,
        ready_timeout=0.1,
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


def test_cmd_send_dispatches_real_command(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="USER",
        target="all",
        message="hi",
        wait_seconds=0.0,
        priority=False,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_send(ns) == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_send_threads_token_to_agent() -> None:
    token = "s3cret"
    async with running_hub(SynapseHub(authenticator=TokenAuthenticator([token]))) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri, token=token)
        try:
            code = await cli_messaging._send(
                uri=uri,
                name="U",
                target="all",
                message="hi",
                wait_seconds=0.0,
                token=token,
            )
            message = await observer.recorder.wait_for(
                lambda item: item.get("type") == "chat" and item.get("sender") == "U"
            )
        finally:
            await close_agents(observer)

    assert code == 0
    assert message["payload"] == "hi"


async def test_send_marks_priority() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        try:
            code = await cli_messaging._send(
                uri=uri,
                name="U",
                target="all",
                message="!",
                wait_seconds=0.0,
                priority=True,
            )
            message = await observer.recorder.wait_for(
                lambda item: item.get("type") == "chat" and item.get("sender") == "U"
            )
        finally:
            await close_agents(observer)

    assert code == 0
    assert message["priority"] is True


async def test_send_normalizes_waiter_identity_before_connecting() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        waiter = await connect_agent("api-dev-rx", uri)
        observer = await connect_agent("OBSERVER", uri)
        try:
            code = await cli_messaging._send(
                uri=uri,
                name="api-dev-rx",
                target="OBSERVER",
                message="ready",
                wait_seconds=0.0,
            )
            message = await observer.recorder.wait_for(
                lambda item: item.get("type") == "chat" and item.get("payload") == "ready"
            )
        finally:
            await close_agents(waiter, observer)

    assert code == 0
    assert message["sender"] == "api-dev"
    assert message["target"] == "OBSERVER"


def test_send_waiter_identity_normalization_is_documented() -> None:
    readme = " ".join((REPO_ROOT / "README.md").read_text(encoding="utf-8").split())
    cli_docs = " ".join((REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8").split())

    assert "synapse send --name api-dev-rx ..." in readme
    assert "sends as `api-dev`" in readme
    assert "one-shot send accidentally uses a waiter name" in cli_docs
    assert "avoids the hub's duplicate-name refusal" in cli_docs

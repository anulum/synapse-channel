# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — rotating outbound peer token files through `synapse hub`
"""The hub options build rotating peer tokens, and a hub process uses a replaced file."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from cli_e2e_helpers import isolated_hub
from cli_processes_helpers import _hub_ns
from hub_e2e_helpers import read_until_type
from key_set_helpers import replace_owner_only
from synapse_channel import cli_processes
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.peer_token import PeerTokenFile, current_peer_token

_ROUTES: dict[str, tuple[str, dict[str, Any]]] = {
    "claim_peer_token": (
        "claim_peers",
        {"namespace_owner": ["THEIRS=syn-b"], "claim_peer": ["syn-b=ws://b:8876"]},
    ),
    "relay_peer_token": (
        "relay_peers",
        {"namespace_owner": ["THEIRS=syn-b"], "relay_peer": ["syn-b=ws://b:8876"]},
    ),
    "message_peer_token": ("message_peers", {"message_peer": ["syn-b=ws://b:8876"]}),
}


def _close_runner(coro: Coroutine[Any, Any, None]) -> None:
    coro.close()


def _start(**options: Any) -> dict[str, Any]:
    """Run hub startup with ``options``; return the hub's constructor arguments."""
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(hub_id="syn-a", **options), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    return captured


@pytest.mark.parametrize("option", sorted(_ROUTES))
def test_a_token_file_option_builds_a_rotating_token_and_inline_wins(
    tmp_path: Path, option: str
) -> None:
    key, route = _ROUTES[option]
    path = tmp_path / "peer.token"
    replace_owner_only(path, "file-token\n")
    peer = _start(**route, **{option + "_file": str(path)})[key]["syn-b"]
    assert isinstance(peer.token, PeerTokenFile) and peer.token.path == path
    replace_owner_only(path, "rotated-token\n")
    assert current_peer_token(peer.token) == "rotated-token"
    inline = _start(**route, **{option: "inline-token", option + "_file": str(path)})[key]
    assert inline["syn-b"].token == "inline-token"


@pytest.mark.parametrize("option", [*sorted(_ROUTES), "multihub_watch_token"])
def test_an_unreadable_token_file_refuses_hub_startup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    route = (
        {"namespace_owner": ["OWNED=syn-a"], "multihub_watch": ["syn-b=ws://b:8876"]}
        if option == "multihub_watch_token"
        else _ROUTES[option][1]
    )
    path = tmp_path / "peer.token"
    replace_owner_only(path, "shared-token\n", mode=0o644)
    ns = _hub_ns(
        db=str(tmp_path / "hub.db"), hub_id="syn-a", **route, **{option + "_file": str(path)}
    )
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    err = capsys.readouterr().err
    assert "--" + option.replace("_", "-") + "-file" in err and "shared-token" not in err
    assert not (tmp_path / "hub.db").exists()
    replace_owner_only(path, "readable-token\n")
    watch = {"namespace_owner": ["OWNED=syn-a"], "multihub_watch": ["syn-b=ws://b:8876"]}
    if option == "multihub_watch_token":
        assert _start(**watch, multihub_watch_token_file=str(path))["namespace_ownership"]


async def test_a_hub_process_sends_the_replaced_peer_token_file_without_restart(
    tmp_path: Path,
) -> None:
    """Replacing the file between two forwards changes the token; the process never restarts."""
    seen: list[object] = []

    async def peer(socket: ServerConnection) -> None:
        seen.append(json.loads(await asyncio.wait_for(socket.recv(), 5)).get("token"))
        await socket.close(code=1011, reason="recording peer")

    path = tmp_path / "peer.token"
    replace_owner_only(path, "token-before-rotation\n")
    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        options = [
            "--hub-id",
            "origin",
            "--message-peer",
            f"peer=ws://127.0.0.1:{port}",
            "--message-peer-token-file",
            str(path),
        ]
        with isolated_hub(tmp_path, extra_args=options) as hub:
            async with connect(hub.uri) as sender:
                await read_until_type(sender, "welcome")
                await sender.send(
                    json.dumps(
                        {"sender": "PROJ/sender", "type": "heartbeat", "protocol_version": 3}
                    )
                )
                for rotate in (False, True):
                    if rotate:
                        replace_owner_only(path, "token-after-rotation\n")
                    await sender.send(
                        json.dumps({"sender": "PROJ/sender", "type": "who_request", "hub": "peer"})
                    )
                    await read_until_type(sender, "error")
    assert seen == ["token-before-rotation", "token-after-rotation"]

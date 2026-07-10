# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — module-owned A2A CLI type contract tests
"""Exercise the shared A2A CLI aliases at runtime and through strict MyPy."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import get_args, get_origin

from mypy import api as mypy_api

from synapse_channel import cli_a2a_types
from synapse_channel.a2a import agent_card_from_manifest
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

ROOT = Path(__file__).resolve().parents[1]


def _run_mypy(path: Path) -> tuple[str, str, int]:
    """Type-check one explicit contract fixture with the repository's strict config."""
    return mypy_api.run(
        [
            "--strict",
            "--config-file",
            str(ROOT / "pyproject.toml"),
            "--no-error-summary",
            str(path),
        ]
    )


async def _answer() -> int:
    """Return a typed value for the generic async-runner contract."""
    return 42


def test_aliases_preserve_callable_result_types() -> None:
    """The runtime aliases expose the result types consumed by A2A command modules."""
    aliases = (
        cli_a2a_types.AsyncRunner,
        cli_a2a_types.A2ACardRunner,
        cli_a2a_types.ManifestFetcher,
        cli_a2a_types.CardBuilder,
        cli_a2a_types.RuntimeFactory,
        cli_a2a_types.BridgeFactory,
        cli_a2a_types.StoreFactory,
        cli_a2a_types.ServerRunner,
    )
    assert all(get_origin(alias) is Callable for alias in aliases)
    async_args = get_args(cli_a2a_types.AsyncRunner)
    coroutine_args = get_args(async_args[0][0])
    assert async_args[1] is coroutine_args[2]
    assert get_args(cli_a2a_types.BridgeFactory)[1] is A2ABridge
    assert get_args(cli_a2a_types.StoreFactory)[1] is A2ATaskStore
    assert get_args(cli_a2a_types.ServerRunner)[1] is None


def test_aliases_drive_real_runner_store_and_card_factories() -> None:
    """Alias-typed production callables retain their concrete runtime contracts."""
    async_runner: cli_a2a_types.AsyncRunner[int] = asyncio.run
    store_factory: cli_a2a_types.StoreFactory = A2ATaskStore
    card_builder: cli_a2a_types.CardBuilder = agent_card_from_manifest
    bridge_factory: cli_a2a_types.BridgeFactory = A2ABridge

    assert async_runner(_answer()) == 42
    assert isinstance(store_factory(), A2ATaskStore)
    card = card_builder([], endpoint_url="https://bridge.example/a2a")
    assert card["supportedInterfaces"][0]["url"] == "https://bridge.example/a2a"
    assert bridge_factory is A2ABridge


def test_strict_mypy_accepts_production_a2a_alias_bindings(tmp_path: Path) -> None:
    """Every production default callable must remain assignable to its shared alias."""
    fixture = tmp_path / "valid_a2a_types.py"
    fixture.write_text(
        """\
import asyncio
from synapse_channel.a2a import agent_card_from_manifest
from synapse_channel.a2a_http import serve_a2a_http
from synapse_channel.a2a_server import A2ABridge, SynapseAgentRuntime
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.cli_a2a_card import _a2a_card
from synapse_channel.cli_a2a_serve import _fetch_manifest
from synapse_channel.cli_a2a_types import (
    A2ACardRunner,
    AsyncRunner,
    BridgeFactory,
    CardBuilder,
    ManifestFetcher,
    RuntimeFactory,
    ServerRunner,
    StoreFactory,
)

async_runner: AsyncRunner[int] = asyncio.run
card_runner: A2ACardRunner = _a2a_card
manifest_fetcher: ManifestFetcher = _fetch_manifest
card_builder: CardBuilder = agent_card_from_manifest
runtime_factory: RuntimeFactory = SynapseAgentRuntime
bridge_factory: BridgeFactory = A2ABridge
store_factory: StoreFactory = A2ATaskStore
server_runner: ServerRunner = serve_a2a_http
""",
        encoding="utf-8",
    )

    stdout, stderr, status = _run_mypy(fixture)
    assert status == 0, stdout + stderr


def test_strict_mypy_rejects_wrong_store_factory_result(tmp_path: Path) -> None:
    """A factory returning the wrong runtime type must fail the public alias boundary."""
    fixture = tmp_path / "invalid_a2a_types.py"
    fixture.write_text(
        """\
from synapse_channel.cli_a2a_types import StoreFactory

def build_store() -> str:
    return "not-a-store"

store_factory: StoreFactory = build_store
""",
        encoding="utf-8",
    )

    stdout, stderr, status = _run_mypy(fixture)
    assert status == 1
    assert "Incompatible types in assignment" in stdout
    assert "A2ATaskStore" in stdout
    assert stderr == ""

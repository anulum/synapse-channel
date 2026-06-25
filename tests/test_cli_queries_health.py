# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only hub query commands (who/state/board/manifest/health)

from __future__ import annotations

import argparse
from typing import Any

import pytest

from cli_queries_helpers import FakeAgent, _factory
from synapse_channel import cli_queries


async def test_health_ok_when_ready() -> None:
    holder: list[FakeAgent] = []
    code = await cli_queries._health(
        uri="ws://h", name="H", agent_factory=_factory(holder, ready=True)
    )
    assert code == 0


async def test_health_fail_when_unreachable() -> None:
    holder: list[FakeAgent] = []
    code = await cli_queries._health(
        uri="ws://h", name="H", agent_factory=_factory(holder, ready=False)
    )
    assert code == 1


async def test_drop_message_is_noop() -> None:
    await cli_queries._drop_message({"type": "x"})  # a no-op callback; must simply not raise


def test_cmd_health_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli_queries, "_health", fake)
    assert cli_queries._cmd_health(argparse.Namespace(uri="ws://h", name="H", token=None)) == 0

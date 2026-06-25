# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only hub query commands (who/state/board/manifest/health)

from __future__ import annotations

import argparse

from hub_e2e_helpers import _free_port, running_hub
from synapse_channel import cli_queries
from synapse_channel.core.hub import SynapseHub


async def test_health_ok_when_ready() -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        code = await cli_queries._health(uri=uri, name="H")
    assert code == 0


async def test_health_fail_when_unreachable() -> None:
    code = await cli_queries._health(
        uri=f"ws://127.0.0.1:{_free_port()}", name="H", ready_timeout=0.1
    )
    assert code == 1


async def test_drop_message_is_noop() -> None:
    await cli_queries._drop_message({"type": "x"})  # a no-op callback; must simply not raise


def test_cmd_health_dispatches_real_probe() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}", name="H", token=None, ready_timeout=0.1
    )
    assert cli_queries._cmd_health(ns) == 1

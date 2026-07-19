# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — built-wheel WebSocket compatibility smoke regressions

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tools.websockets_compatibility_smoke import assert_package_outside, run_smoke

from synapse_channel.core.hub import SynapseHub


def test_package_source_guard_rejects_checkout_import(tmp_path: Path) -> None:
    package = tmp_path / "src" / "synapse_channel" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.touch()

    with pytest.raises(RuntimeError, match="forbidden source root"):
        assert_package_outside(str(package), tmp_path / "src")


def test_current_websockets_runs_live_hub_and_http_surfaces() -> None:
    result = asyncio.run(run_smoke())

    assert result["hub"] == "ok"
    assert result["health"] == "ok"
    assert result["metrics"] == "ok"
    assert result["synapse_channel"]
    assert result["websockets"]


async def test_websockets_13_non_upgrade_callback_is_not_registered() -> None:
    hub = SynapseHub()
    with patch.object(hub._connection, "handler", new_callable=AsyncMock) as connection_handler:
        await hub.handler(SimpleNamespace(response=SimpleNamespace(status_code=200)))

        connection_handler.assert_not_awaited()

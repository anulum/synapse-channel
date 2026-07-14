# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the process CLI runtime helper

from __future__ import annotations

import pytest

from synapse_channel.cli_processes_runtime import _run


def test_run_executes_the_coroutine_to_completion() -> None:
    ran: list[str] = []

    async def _work() -> None:
        ran.append("done")

    _run(_work())
    assert ran == ["done"]


def test_run_propagates_coroutine_exceptions() -> None:
    async def _boom() -> None:
        raise RuntimeError("runtime failed")

    with pytest.raises(RuntimeError, match="runtime failed"):
        _run(_boom())

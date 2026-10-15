# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — same-identity command exclusion
"""A second supervised command cannot treat an existing lease as its own renewal."""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import run_cli
from hub_e2e_helpers import running_hub
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from test_cli_lock_supervision import renewal_proxy, wait_started


@pytest.mark.parametrize("secured", [False, True])
@pytest.mark.parametrize("replayed", [False, True])
async def test_second_command_with_same_identity_cannot_enter_or_release_the_first(
    tmp_path: Path,
    secured: bool,
    replayed: bool,
) -> None:
    token = secrets.token_urlsafe(32) if secured else ""
    name = "shared/owner"
    environment = {
        "SYNAPSE_TOKEN": token,
        "SYNAPSE_TOKEN_FILE": "",
        "SYN_HOME": str(tmp_path / "home"),
    }
    started, finish, second = (tmp_path / part for part in ("started", "finish", "second"))
    child = (
        "import sys,time;from pathlib import Path\nPath(sys.argv[1]).touch()\n"
        "while not Path(sys.argv[2]).exists(): time.sleep(.01)"
    )
    auth = TokenAuthenticator({token: [name]}) if secured else None
    async with (
        running_hub(SynapseHub(authenticator=auth, default_ttl_seconds=30)) as (hub, direct),
        renewal_proxy(direct, "replay-acquisition" if replayed else "pass") as uri,
    ):
        first = asyncio.create_task(
            asyncio.to_thread(
                run_cli,
                "lock",
                "same-owner",
                "--name",
                name,
                "--wait-timeout",
                "0",
                "--",
                sys.executable,
                "-c",
                child,
                str(started),
                str(finish),
                uri=uri,
                env=environment,
            )
        )
        try:
            await wait_started(started, first)
            original = hub.state.claims["same-owner"].as_persisted_dict()
            contender = await asyncio.to_thread(
                run_cli,
                "lock",
                "same-owner",
                "--name",
                name,
                "--wait-timeout",
                "0",
                "--",
                sys.executable,
                "-c",
                "import sys;from pathlib import Path;Path(sys.argv[1]).touch()",
                str(second),
                uri=uri,
                env=environment,
            )
            assert contender.returncode == 1, contender.output
            assert not second.exists()
            assert not first.done()
            assert hub.state.claims["same-owner"].as_persisted_dict() == original
        finally:
            finish.touch()
            result = await asyncio.wait_for(first, 10)
        assert result.returncode == 0, result.output
        assert "same-owner" not in hub.state.claims


async def test_waiting_same_identity_yields_the_name_for_renewal_and_release(
    tmp_path: Path,
) -> None:
    token = secrets.token_urlsafe(32)
    name = "shared/owner"
    started, finish, second = (tmp_path / part for part in ("started", "finish", "second"))
    environment = {
        "SYNAPSE_TOKEN": token,
        "SYNAPSE_TOKEN_FILE": "",
        "SYN_HOME": str(tmp_path / "home"),
    }
    child = (
        "import sys,time;from pathlib import Path\nPath(sys.argv[1]).touch()\n"
        "while not Path(sys.argv[2]).exists(): time.sleep(.01)"
    )
    async with running_hub(
        SynapseHub(
            authenticator=TokenAuthenticator({token: [name]}),
            default_ttl_seconds=30,
        )
    ) as (hub, uri):
        first = asyncio.create_task(
            asyncio.to_thread(
                run_cli,
                "lock",
                "same-owner",
                "--name",
                name,
                "--",
                sys.executable,
                "-c",
                child,
                str(started),
                str(finish),
                uri=uri,
                env=environment,
                timeout=25,
            )
        )
        contender = None
        try:
            await wait_started(started, first)
            original_expiry = hub.state.claims["same-owner"].lease_expires_at
            contender = asyncio.create_task(
                asyncio.to_thread(
                    run_cli,
                    "lock",
                    "same-owner",
                    "--name",
                    name,
                    "--wait-timeout",
                    "15",
                    "--",
                    sys.executable,
                    "-c",
                    "import sys;from pathlib import Path;Path(sys.argv[1]).touch()",
                    str(second),
                    uri=uri,
                    env=environment,
                    timeout=25,
                )
            )
            await asyncio.sleep(6.5)
            assert not first.done() and not contender.done()
            assert not second.exists()
            assert hub.state.claims["same-owner"].lease_expires_at > original_expiry
        finally:
            finish.touch()
            first_result = await asyncio.wait_for(first, 10)
            second_result = await asyncio.wait_for(contender, 10) if contender is not None else None
        assert first_result.returncode == 0, first_result.output
        assert second_result is not None and second_result.returncode == 0
        assert second.exists()
        assert "same-owner" not in hub.state.claims

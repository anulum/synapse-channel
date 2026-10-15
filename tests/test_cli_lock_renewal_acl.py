# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — scoped renewal ACL enforcement
"""Keep current path authorization across renewals and a real policy restart."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import git_repo, run_cli
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.acl import CLAIM, RELEASE, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


@pytest.mark.parametrize("root_path", ["", " "])
async def test_renewal_cannot_omit_revoked_path_permission_after_restart(
    tmp_path: Path,
    root_path: str,
) -> None:
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (origin, uri):
            owner = await connect_agent("owner", uri)
            try:
                await owner.agent.claim("scoped", worktree="repo", paths=["src"])
                granted = await owner.recorder.wait_for(
                    lambda row: row.get("type") == MessageType.CLAIM_GRANTED
                )
                original = origin.state.claims["scoped"].as_dict()
            finally:
                await close_agents(owner)
        restricted = SynapseHub(
            journal=journal,
            acl_policy=AclPolicy([AclRule(CLAIM, "claim", "*")]),
            require_acl=True,
        )
        async with running_hub(restricted) as (_, uri):
            owner = await connect_agent("owner", uri)
            try:
                await owner.agent.send_message(
                    MessageType.CLAIM,
                    target="System",
                    payload="scoped",
                    task_id="scoped",
                    renew_epoch=granted["epoch"],
                    renewal_id="omitted-path",
                )
                missing = await owner.recorder.wait_for(
                    lambda row: row.get("renewal_id") == "omitted-path"
                )
                assert missing["type"] == MessageType.CLAIM_DENIED
                await owner.agent.send_message(
                    MessageType.CLAIM,
                    target="System",
                    payload="scoped",
                    task_id="scoped",
                    paths=["src"],
                    renew_epoch=granted["epoch"],
                    renewal_id="revoked-path",
                )
                denied = await owner.recorder.wait_for(
                    lambda row: row.get("payload") == "access denied: claim on path:src"
                )
                assert denied["type"] == MessageType.ERROR
                await owner.agent.claim("whole-worktree", worktree="repo", paths=[root_path])
                whole = await owner.recorder.wait_for(
                    lambda row: row.get("payload") == "access denied: claim on path:"
                )
                assert whole["type"] == MessageType.ERROR
                assert "whole-worktree" not in restricted.state.claims
                assert restricted.state.claims["scoped"].as_dict() == original
            finally:
                await close_agents(owner)
    finally:
        journal.close()


async def test_scoped_command_repeats_its_authorized_paths_on_renewal(
    tmp_path: Path,
) -> None:
    requested = path_rule = "src"
    repo = git_repo(tmp_path / "repo")
    (repo / "src").mkdir()
    journal = EventStore(tmp_path / "hub.db")
    try:
        policy = AclPolicy(
            [
                AclRule(CLAIM, "claim", "*"),
                AclRule(CLAIM, "path", path_rule),
                AclRule(RELEASE, "claim", "*"),
            ]
        )
        async with running_hub(
            SynapseHub(journal=journal, acl_policy=policy, require_acl=True)
        ) as (hub, uri):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "scoped",
                "--name",
                "owner",
                "--paths",
                requested,
                "--",
                sys.executable,
                "-c",
                "import time;time.sleep(6.5)",
                uri=uri,
                cwd=repo,
            )
            assert result.returncode == 0, result.output
            claims = [row for row in journal.iter_events() if row.kind == "claim"]
            assert len(claims) >= 2
            assert {tuple(row.payload["paths"]) for row in claims} == {(path_rule,)}
            assert not hub.state.claims
    finally:
        journal.close()


async def test_whole_worktree_renewal_repeats_the_explicit_root_acl_target() -> None:
    policy = AclPolicy([AclRule(CLAIM, "claim", "*"), AclRule(CLAIM, "path", "")])
    async with running_hub(SynapseHub(acl_policy=policy, require_acl=True)) as (hub, uri):
        owner = await connect_agent("owner", uri)
        try:
            await owner.agent.claim("root", worktree="repo", paths=[""])
            original = await owner.recorder.wait_for(
                lambda row: row.get("type") == MessageType.CLAIM_GRANTED
            )
            for paths, renewal_id, verdict in (
                ([], "omitted", MessageType.CLAIM_DENIED),
                ([""], "explicit", MessageType.CLAIM_GRANTED),
            ):
                await owner.agent.send_message(
                    MessageType.CLAIM,
                    target="System",
                    payload="root",
                    task_id="root",
                    paths=paths,
                    renew_epoch=original["epoch"],
                    renewal_id=renewal_id,
                )

                def matches(row: dict[str, object], expected: str = renewal_id) -> bool:
                    return row.get("renewal_id") == expected

                reply = await owner.recorder.wait_for(matches)
                assert reply["type"] == verdict
                assert hub.state.claims["root"].paths == ("",)
                assert hub.state.claims["root"].epoch == original["epoch"]
        finally:
            await close_agents(owner)

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for planning wire project scope and version CAS

from __future__ import annotations

from hub_e2e_helpers import close_agents, connect_agent, running_hub


async def test_declare_with_project_broadcasts_scope_and_version() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await poster.agent.post_task("T1", "Parser", project="SYNAPSE-CHANNEL")
            posted = await watcher.recorder.wait_for(
                lambda m: m.get("type") == "ledger_task_posted"
            )
            assert posted["task"]["project"] == "SYNAPSE-CHANNEL"
            assert posted["task"]["version"] == 1
            assert hub.blackboard.tasks["T1"].project == "SYNAPSE-CHANNEL"
        finally:
            await close_agents(poster, watcher)


async def test_declare_without_new_fields_keeps_legacy_shape() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "Parser")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_posted")
            task = hub.blackboard.tasks["T1"]
            assert task.project == ""
            assert task.version == 1
        finally:
            await close_agents(poster)


async def test_update_with_matching_expected_version_is_accepted() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "Parser", project="PROJ")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_posted")
            await poster.agent.update_ledger_task(
                "T1", suggested_owner="PROJ/kimi-3dcd", expected_version=1
            )
            updated = await poster.recorder.wait_for(
                lambda m: m.get("type") == "ledger_task_updated"
            )
            assert updated["task"]["suggested_owner"] == "PROJ/kimi-3dcd"
            assert updated["task"]["version"] == 2
        finally:
            await close_agents(poster)


async def test_update_with_stale_expected_version_fails_without_mutation() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "Parser")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_posted")
            await poster.agent.update_ledger_task("T1", suggested_owner="X", expected_version=9)
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "version conflict" in error["payload"]
            task = hub.blackboard.tasks["T1"]
            assert task.suggested_owner == ""
            assert task.version == 1
        finally:
            await close_agents(poster)


async def test_update_with_mistyped_expected_version_fails_closed() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "Parser")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_posted")
            await poster.agent.send_message(
                "ledger_task_update", task_id="T1", expected_version="1"
            )
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "expected_version" in error["payload"]
            assert "integer" in error["payload"]
            assert hub.blackboard.tasks["T1"].version == 1
        finally:
            await close_agents(poster)


async def test_declare_with_mistyped_expected_version_fails_closed() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.send_message(
                "ledger_task", task_id="T1", title="Parser", expected_version="0"
            )
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "expected_version" in error["payload"]
            assert "integer" in error["payload"]
            assert "T1" not in hub.blackboard.tasks
        finally:
            await close_agents(poster)


async def test_update_with_boolean_expected_version_fails_closed() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "Parser")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_posted")
            await poster.agent.send_message(
                "ledger_task_update", task_id="T1", expected_version=True
            )
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "integer" in error["payload"]
            assert hub.blackboard.tasks["T1"].version == 1
        finally:
            await close_agents(poster)


async def test_update_project_scope_is_broadcast() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await poster.agent.post_task("T1", "Parser")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_posted")
            await poster.agent.update_ledger_task("T1", project="SYNAPSE-CHANNEL")
            updated = await watcher.recorder.wait_for(
                lambda m: m.get("type") == "ledger_task_updated"
            )
            assert updated["task"]["project"] == "SYNAPSE-CHANNEL"
            assert updated["task"]["version"] == 2
        finally:
            await close_agents(poster, watcher)


async def test_declare_project_conflict_is_reported_to_sender() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "Parser", project="PROJ")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_posted")
            await poster.agent.post_task("T1", "Parser", project="OTHER")
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "project conflict" in error["payload"]
            assert hub.blackboard.tasks["T1"].project == "PROJ"
            assert hub.blackboard.tasks["T1"].version == 1
        finally:
            await close_agents(poster)


async def test_declare_expected_version_zero_creates_only_once() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "Parser", expected_version=0)
            posted = await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_posted")
            assert posted["task"]["version"] == 1
            await poster.agent.post_task("T1", "Parser", expected_version=0)
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "version conflict" in error["payload"]
            assert hub.blackboard.tasks["T1"].version == 1
        finally:
            await close_agents(poster)

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for task CLI project scope and version CAS flags

from __future__ import annotations

import argparse
import asyncio

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel import cli, cli_tasks
from synapse_channel.core.hub import SynapseHub


def test_parser_task_declare_project_and_expected_version() -> None:
    args = cli.build_parser().parse_args(
        [
            "task",
            "declare",
            "BUILD",
            "--title",
            "Compile",
            "--project",
            "SYNAPSE-CHANNEL",
            "--expected-version",
            "0",
        ]
    )
    assert args.task_id == "BUILD"
    assert args.project == "SYNAPSE-CHANNEL"
    assert args.expected_version == 0
    assert args.func is cli_tasks._cmd_task_declare


def test_parser_task_update_project_and_expected_version() -> None:
    args = cli.build_parser().parse_args(
        ["task", "update", "BUILD", "--project", "PROJ", "--expected-version", "3"]
    )
    assert args.project == "PROJ"
    assert args.expected_version == 3
    assert args.func is cli_tasks._cmd_task_update


def test_parser_task_new_flags_default_cleanly() -> None:
    declare = cli.build_parser().parse_args(["task", "declare", "BUILD"])
    assert declare.project == ""
    assert declare.expected_version is None
    update = cli.build_parser().parse_args(["task", "update", "BUILD"])
    assert update.project is None
    assert update.expected_version is None


async def test_cmd_task_declare_with_project_sets_scope() -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        ns = argparse.Namespace(
            task_id="BUILD",
            title="Compile",
            depends_on=None,
            project="SYNAPSE-CHANNEL",
            expected_version=None,
            uri=uri,
            name="P",
            token=None,
        )
        code = await asyncio.to_thread(cli_tasks._cmd_task_declare, ns)
        assert code == 0
        assert hub.blackboard.tasks["BUILD"].project == "SYNAPSE-CHANNEL"
        assert hub.blackboard.tasks["BUILD"].version == 1


async def test_cmd_task_update_with_matching_expected_version() -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        hub.blackboard.post_task(task_id="BUILD", title="Compile", author="seed")
        ns = argparse.Namespace(
            task_id="BUILD",
            status=None,
            suggested_owner="PROJ/kimi-3dcd",
            project=None,
            expected_version=1,
            uri=uri,
            name="P",
            token=None,
        )
        code = await asyncio.to_thread(cli_tasks._cmd_task_update, ns)
        assert code == 0
        task = hub.blackboard.tasks["BUILD"]
        assert task.suggested_owner == "PROJ/kimi-3dcd"
        assert task.version == 2


async def test_cmd_task_declare_without_flags_keeps_legacy_defaults() -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        ns = argparse.Namespace(
            task_id="BUILD",
            title="Compile",
            depends_on=None,
            uri=uri,
            name="P",
            token=None,
        )
        code = await asyncio.to_thread(cli_tasks._cmd_task_declare, ns)
        assert code == 0
        task = hub.blackboard.tasks["BUILD"]
        assert task.project == ""
        assert task.version == 1


async def test_cmd_task_update_with_stale_expected_version_fails_loudly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        hub.blackboard.post_task(task_id="BUILD", title="Compile", author="seed")
        ns = argparse.Namespace(
            task_id="BUILD",
            status=None,
            suggested_owner="PROJ/kimi-3dcd",
            project=None,
            expected_version=9,
            uri=uri,
            name="P",
            token=None,
        )
        code = await asyncio.to_thread(cli_tasks._cmd_task_update, ns)
        assert code == 1
        assert "version conflict" in capsys.readouterr().out
        task = hub.blackboard.tasks["BUILD"]
        assert task.suggested_owner == ""
        assert task.version == 1

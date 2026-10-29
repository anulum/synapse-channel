# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — complete native pytest collection and indexed execution receipts
"""Partition pytest's actual collected node IDs and record completed executions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import cast

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register an explicit, all-or-nothing indexed shard profile."""
    group = parser.getgroup("complete-shards")
    group.addoption("--shard-index", type=int)
    group.addoption("--shard-count", type=int)
    group.addoption("--shard-output", type=Path)


def pytest_configure(config: pytest.Config) -> None:
    """Reject partial or unbound profiles before executing tests."""
    index = cast("int | None", config.getoption("shard_index"))
    count = cast("int | None", config.getoption("shard_count"))
    output = cast("Path | None", config.getoption("shard_output"))
    if index is None and count is None and output is None:
        return
    source = os.environ.get("GITHUB_SHA", "")
    if (
        index is None
        or count is None
        or output is None
        or not 1 <= count <= 128
        or not 0 <= index < count
        or re.fullmatch(r"[0-9a-f]{40}", source) is None
    ):
        raise pytest.UsageError("shards require an index, count, output and exact GITHUB_SHA")
    config.pluginmanager.register(ShardRecorder(config, index, count, output, source))


class ShardRecorder:
    """Keep the complete collection and require teardown completion for every selected ID."""

    def __init__(
        self, config: pytest.Config, index: int, count: int, output: Path, source: str
    ) -> None:
        self.config = config
        self.index = index
        self.count = count
        self.output = output
        self.source = source
        self.inventory: list[str] = []
        self.selected: list[str] = []
        self.completed: set[str] = set()
        self.skipped: set[str] = set()
        self.called: set[str] = set()
        self.failed = False

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        """Round-robin the complete sorted native collection without changing item order."""
        self.inventory = sorted(item.nodeid for item in items)
        if len(set(self.inventory)) != len(self.inventory):
            raise pytest.UsageError("shard inventory contains duplicate node IDs")
        self.selected = self.inventory[self.index :: self.count]
        if not self.selected:
            raise pytest.UsageError("shard has no collected tests")
        chosen = set(self.selected)
        removed = [item for item in items if item.nodeid not in chosen]
        items[:] = [item for item in items if item.nodeid in chosen]
        self.config.hook.pytest_deselected(items=removed)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Observe actual pytest outcomes, including setup and teardown failures."""
        self.failed |= report.failed
        if report.skipped:
            self.skipped.add(report.nodeid)
        if report.when == "call":
            self.called.add(report.nodeid)
        if report.when == "teardown":
            self.completed.add(report.nodeid)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        """Refuse early successful exit and retain an honest failure receipt."""
        success = (
            exitstatus == 0
            and not self.failed
            and bool(self.selected)
            and self.completed == set(self.selected)
        )
        if exitstatus == 0 and not success:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        config_bytes = self.config.inipath.read_bytes() if self.config.inipath else b""
        record = {
            "schema": 1,
            "source": self.source,
            "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
            "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "index": self.index,
            "count": self.count,
            "inventory": self.inventory,
            "selected": self.selected,
            "completed": sorted(self.completed),
            "called": sorted(self.called),
            "skipped": sorted(self.skipped),
            "success": success,
            "exitstatus": int(session.exitstatus),
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

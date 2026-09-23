# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — reviewed repository lure regression
"""Exercise public discovery CLI rejection using the reviewed host identities."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from tools import vendor_discovery as discovery
from tools import vendor_host_inspection as inspection

REVIEWED_HOSTS = (
    "alucard1718/autonomous-code-sandbox",
    "daya7781r/miii-cli-offline-coder",
    "imlalitrajputs2/tankpkg-skill-vault",
    "joynb/program-surgeon",
    "khankamraan2006-crypto/fabric-router-core",
    "labir12/oh-my-pi-ai-toolbelt",
    "miguel-guimaray/pi-memory-adaptive-log",
    "minidupabasara2024-ship-it/py-trio-workflow",
    "mnni43353-hue/pi-model-router-cloudsync",
    "pepinorancio1/sticky-switcher-funnel-playbook",
    "ryckycarrizo/pi-memory-retrieval",
)
BLOB = ".github/AbCdEfGhIjKlMn"
WORKFLOW = ".github/workflows/update.yml"


def _encoded(text: str) -> dict[str, str]:
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def _host(name: str) -> dict[str, Any]:
    owner, project = name.split("/")
    return {
        "full_name": name,
        "name": project,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{name}",
        "updated_at": "2026-09-23T00:00:00Z",
        "pushed_at": "2026-09-23T00:00:00Z",
        "default_branch": "main",
        "has_pages": True,
        "language": "HTML",
        "license": None,
    }


def _api(name: str) -> dict[str, Any]:
    owner, project = name.split("/")
    base = f"https://api.github.com/repos/{name}"
    return {
        f"{base}/git/trees/main?recursive=1": {
            "tree": [
                {"path": path, "type": "blob"}
                for path in ("README.md", "index.html", BLOB, WORKFLOW)
            ],
            "truncated": False,
        },
        f"{base}/contents/README.md": _encoded(
            f"# {project}\nDownload Link: https://{owner}.github.io/{project}/\n"
        ),
        f"{base}/contents/{WORKFLOW}": _encoded(
            f"on:\n  schedule:\n    - cron: '*/5 * * * *'\n"
            f"permissions:\n  contents: write\n"
            "jobs:\n  update:\n    steps:\n"
            f"      - run: git -c user.name=bot commit -m update {BLOB}\n"
        ),
    }


def test_reviewed_hosts_rejected_by_public_cli(tmp_path: Path) -> None:
    """Each reviewed lure is excluded from C15 by its inspectable structure."""
    api: dict[str, Any] = {}
    for name in REVIEWED_HOSTS:
        api.update(_api(name))
    fixture = {
        "models_dev": {},
        "github_hosts": {
            "items": [_host(name) for name in REVIEWED_HOSTS],
            "total_count": len(REVIEWED_HOSTS),
            "incomplete_results": False,
        },
        "mcp_registry": [{"servers": [], "metadata": {"count": 0}}],
        "github_api": api,
    }
    source = tmp_path / "source.json"
    source.write_text(json.dumps(fixture), encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"schema":1,"checked_at":null,"sources":{},"candidates":{}}')
    report = tmp_path / "report.json"
    proposal = tmp_path / "proposal.json"
    result = subprocess.run(
        [
            sys.executable,
            "tools/vendor_discovery.py",
            "--fixture",
            str(source),
            "--catalog",
            str(catalog),
            "--report",
            str(report),
            "--next-catalog",
            str(proposal),
            "--strict",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = json.loads(report.read_text())["review_queue"]
    assert {row["key"] for row in rows} == {f"host:{name}" for name in REVIEWED_HOSTS}
    assert all(row["status"] == "rejected" and row["suggested_lane"] is None for row in rows)
    assert all(row["evidence"][0]["claims"]["inspection"] == "rejected_lure" for row in rows)
    repeat = tmp_path / "repeat.json"
    repeat_proposal = tmp_path / "repeat-proposal.json"
    repeated = subprocess.run(
        [
            sys.executable,
            "tools/vendor_discovery.py",
            "--fixture",
            str(source),
            "--catalog",
            str(proposal),
            "--report",
            str(repeat),
            "--next-catalog",
            str(repeat_proposal),
            "--strict",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeat.read_text())["counts"]["review_queue"] == 0
    assert all(
        row["status"] == "rejected"
        for row in json.loads(repeat_proposal.read_text())["candidates"].values()
    )


def test_positive_source_and_broken_repository_are_distinct() -> None:
    """A source tree outranks fresh lures; an MCP 404 remains ineligible."""
    host = _host("established/agent")
    host["has_pages"] = False
    host["license"] = {"spdx_id": "MIT"}
    result = inspection.inspect_host(
        host,
        fetch=lambda url: {"truncated": False, "tree": [{"path": "src/agent.py", "type": "blob"}]},
    )
    assert result == {"code_file_count": 1, "inspection": "code_present"}

    def missing(url: str) -> dict[str, Any]:
        raise inspection.RepositoryMissing("absent")

    assert (
        inspection.repository_reachability("https://github.com/a2awire/nonexistent", fetch=missing)
        == "unreachable"
    )
    assert (
        inspection.repository_reachability(
            "https://github.com/a2awire/old-name",
            fetch=lambda _url: {"full_name": "a2awire/new-name"},
        )
        == "unreachable"
    )
    assert (
        inspection.repository_reachability("https://other.example/a2awire/nonexistent")
        == "unsupported_url"
    )


def test_mcp_broken_repository_is_recorded_and_excluded() -> None:
    """The reviewed a2awire 404 survives the collection and review path."""
    name = "com.a2awire/benchmark-resume-review-2026-09-19-7185eb28"
    fixture = {
        "models_dev": {},
        "github_hosts": {"items": [], "total_count": 0, "incomplete_results": False},
        "mcp_registry": [
            {
                "servers": [
                    {
                        "server": {
                            "name": name,
                            "title": name,
                            "version": "1.0.0",
                            "repository": {"url": "https://github.com/ee324/a2awire"},
                            "remotes": [],
                        },
                        "_meta": {
                            "io.modelcontextprotocol.registry/official": {"status": "active"}
                        },
                    }
                ],
                "metadata": {"count": 1},
            }
        ],
        "github_api": {"https://api.github.com/repos/ee324/a2awire": {"_http_status": 404}},
    }
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    rows, errors, complete = discovery.collect(fixture=fixture, now=now)
    assert errors == {}
    assert complete["mcp_repository_check"] is True
    config, previous = discovery.load_inputs(discovery.DEFAULT_CONFIG, discovery.DEFAULT_CATALOG)
    report, state = discovery.reconcile(config, previous, rows, errors, complete, now=now)
    matching = [row for row in report["review_queue"] if row["key"] == f"mcp:{name}"]
    assert len(matching) == 1
    assert matching[0]["status"] == "hold_broken_provenance"
    assert matching[0]["suggested_lane"] is None
    assert matching[0]["evidence"][0]["claims"]["repository_reachability"] == "unreachable"
    repeated_rows, errors, complete = discovery.collect(fixture=fixture, now=now, previous=state)
    repeat, next_state = discovery.reconcile(
        config, state, repeated_rows, errors, complete, now=now
    )
    assert not any(row["key"] == f"mcp:{name}" for row in repeat["review_queue"])
    assert next_state["candidates"][f"mcp:{name}"]["status"] == "hold_broken_provenance"


def test_missing_or_malformed_lure_evidence_fails_closed() -> None:
    host = _host(REVIEWED_HOSTS[0])
    api = _api(REVIEWED_HOSTS[0])
    api.pop(f"https://api.github.com/repos/{REVIEWED_HOSTS[0]}/contents/{WORKFLOW}")

    def fetch(url: str) -> dict[str, Any]:
        if url not in api:
            raise inspection.InspectionError("unavailable")
        return cast(dict[str, Any], api[url])

    with pytest.raises(inspection.InspectionError):
        inspection.inspect_host(host, fetch=fetch)

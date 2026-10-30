# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — measure contract conflicts against real merged integration errors
"""Count caught, missed and falsely blocked contract conflicts.

Each scenario is a real disposable git repository with real branches. The
contract check (:mod:`synapse_channel.git.contract_conflicts`) runs on the
branches first. Then, separately, the branches are really merged in a scratch
clone, and the integration is judged by a check independent of the digests:
every client file must call only endpoints that the merged spec defines, and
every route the merged deployment serves must be one the merged spec defines.
So:

* caught: the check flags it and the merge breaks;
* missed: the merge breaks and the check is silent;
* false block: the check flags it and the merge works;
* lost parallelism: of the scenarios whose merge works, how many a policy of
  blocking on any finding would have stopped;
* refused: the check declines the input (here, a branch that drops a
  declaration), which is neither a pass nor a finding.

The scenarios include cases the check is known to get wrong: an additive spec
change and a whitespace-only change are false blocks, and a client or a
deployment nobody declared in ``contracts.json`` is a miss. The composed
scenarios join an interface change, a client built on the old version and a
deployment, on separate branches with no shared file. The numbers say what
byte identity costs; they are not tuned.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.git.contract_conflicts import ContractError, check, digest

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "contract_conflict_benchmark.json"
CLIENT = {"kind": "client", "path": "clients/python/", "record": "clients/python/CONTRACT"}
DEPLOYMENT = {"kind": "deployment", "path": "deploy/", "record": "deploy/CONTRACT"}


def manifest(*public_dependents: Mapping[str, str]) -> str:
    """Return the declared contracts: the public API with these dependents, and an admin API."""
    return json.dumps(
        {
            "contracts": [
                {
                    "name": "public-api",
                    "spec": "api/spec.yaml",
                    "dependents": [dict(item) for item in public_dependents],
                },
                {"name": "admin-api", "spec": "admin/spec.yaml"},
            ]
        },
        indent=2,
    )


MANIFEST = manifest(CLIENT)
"""The default declaration: the public API with one client; the deployment is undeclared."""
DEPLOYED = manifest(CLIENT, DEPLOYMENT)
"""The declaration of the composed scenarios: a client and a deployment."""
_PATH = re.compile(r"^  (/[a-z]+):", re.MULTILINE)
_CALL = re.compile(r"'(/[a-z]+)'")
_ROUTE = re.compile(r"^route (/[a-z]+)$", re.MULTILINE)


def spec(*paths: str, trailer: str = "") -> bytes:
    """Return a spec defining ``paths``, optionally with trailing bytes."""
    return ("paths:\n" + "".join(f"  {path}: {{}}\n" for path in paths) + trailer).encode()


def client(*calls: str) -> str:
    """Return a client file that calls ``calls``."""
    return "CALLS = [" + ", ".join(f"'{call}'" for call in calls) + "]\n"


def record(data: bytes) -> str:
    """Return the record line of a dependent built against ``data``."""
    return f"public-api {digest(data)}\n"


def routes(*paths: str, replicas: int = 2) -> str:
    """Return a deployment that serves ``paths`` with ``replicas`` instances."""
    return f"replicas {replicas}\n" + "".join(f"route {path}\n" for path in paths)


V1 = spec("/ping", "/pong")
BASE: dict[str, str | bytes] = {
    "contracts.json": MANIFEST,
    "api/spec.yaml": V1,
    "admin/spec.yaml": spec("/users"),
    "clients/python/generated.py": client("/ping", "/pong"),
    "clients/python/usage.py": client("/pong"),
    "clients/python/CONTRACT": record(V1),
    "clients/js/usage.py": client("/pong"),
    "deploy/service.txt": routes("/pong"),
    "deploy/CONTRACT": record(V1),
}
"""The base: a generated client, hand-written usage, an undeclared JS client and a deployment."""


@dataclass(frozen=True)
class Scenario:
    """Branches that each change some files on top of :data:`BASE`; no two share a file."""

    name: str
    branches: Mapping[str, Mapping[str, str | bytes]]
    declaration: str = MANIFEST


def _changed_api(
    *paths: str, trailer: str = "", deployment: bool = False
) -> dict[str, str | bytes]:
    """Return a branch that changes the spec and regenerates the declared dependents against it."""
    new = spec(*paths, trailer=trailer)
    files: dict[str, str | bytes] = {
        "api/spec.yaml": new,
        "clients/python/generated.py": client(*paths),
        "clients/python/CONTRACT": record(new),
    }
    if deployment:
        files["deploy/CONTRACT"] = record(new)
    return files


SCENARIOS = (
    Scenario(
        "removal_vs_usage_built_on_old",
        {"api": _changed_api("/pong"), "usage": {"clients/python/usage.py": client("/ping")}},
    ),
    Scenario(
        "addition_vs_usage_built_on_old",
        {
            "api": _changed_api("/ping", "/pong", "/peek"),
            "usage": {"clients/python/usage.py": client("/ping", "/pong")},
        },
    ),
    Scenario(
        "whitespace_vs_usage_edit",
        {
            "api": _changed_api("/ping", "/pong", trailer="\n"),
            "usage": {"clients/python/usage.py": client("/ping")},
        },
    ),
    Scenario(
        "removal_vs_usage_avoiding_the_removed_path",
        {
            "api": _changed_api("/pong"),
            "usage": {"clients/python/usage.py": client("/pong", "/pong")},
        },
    ),
    Scenario("removal_without_regeneration", {"api": {"api/spec.yaml": spec("/pong")}}),
    Scenario(
        "removal_and_unrelated_docs",
        {"api": _changed_api("/pong"), "docs": {"README.md": "docs\n"}},
    ),
    Scenario(
        "two_independent_contracts",
        {
            "api": _changed_api("/ping", "/pong", "/peek"),
            "admin": {"admin/spec.yaml": spec("/users", "/roles")},
        },
    ),
    Scenario(
        "removal_vs_undeclared_prefix_sibling",
        {
            "api": _changed_api("/pong"),
            "sibling": {"clients/python-extra/usage.py": client("/ping")},
        },
    ),
    Scenario(
        "removal_with_malformed_record",
        {
            "api": {
                "api/spec.yaml": spec("/pong"),
                "clients/python/generated.py": client("/pong"),
                "clients/python/CONTRACT": "public-api latest\n",
            }
        },
    ),
    Scenario(
        "declaration_dropped_by_branch",
        {
            "api": {
                "api/spec.yaml": spec("/pong"),
                "contracts.json": json.dumps(
                    {"contracts": [{"name": "public-api", "spec": "api/spec.yaml"}]}
                ),
            },
        },
    ),
    Scenario(
        "removal_vs_undeclared_client",
        {"api": _changed_api("/pong"), "js": {"clients/js/usage.py": client("/ping")}},
    ),
    Scenario(
        "composed_removal_vs_deployment_built_on_old",
        {
            "api": _changed_api("/pong", deployment=True),
            "deploy": {"deploy/service.txt": routes("/pong", "/ping")},
        },
        DEPLOYED,
    ),
    Scenario(
        "composed_removal_vs_client_and_deployment_built_on_old",
        {
            "api": _changed_api("/pong", deployment=True),
            "usage": {"clients/python/usage.py": client("/ping")},
            "deploy": {"deploy/service.txt": routes("/pong", "/ping")},
        },
        DEPLOYED,
    ),
    Scenario(
        "composed_addition_vs_deployment_scaling",
        {
            "api": _changed_api("/ping", "/pong", "/peek", deployment=True),
            "deploy": {"deploy/service.txt": routes("/pong", replicas=4)},
        },
        DEPLOYED,
    ),
    Scenario(
        "removal_vs_undeclared_deployment",
        {
            "api": _changed_api("/pong"),
            "deploy": {"deploy/service.txt": routes("/pong", "/ping")},
        },
    ),
)
"""Labelled cases, including ones the check is known to get wrong."""


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` with an isolated identity and configuration."""
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "bench",
        "GIT_AUTHOR_EMAIL": "bench@example.test",
        "GIT_COMMITTER_NAME": "bench",
        "GIT_COMMITTER_EMAIL": "bench@example.test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=environment, check=True
    )
    return done.stdout


def _write(repo: Path, files: Mapping[str, str | bytes]) -> None:
    """Write ``files`` below ``repo``."""
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    """Commit everything in ``repo``."""
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", message)


def build(root: Path, scenario: Scenario) -> Path:
    """Create the scenario's repository with its base and one commit per branch."""
    repo = root / scenario.name
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _write(repo, {**BASE, "contracts.json": scenario.declaration})
    _commit(repo, "base")
    for branch, files in scenario.branches.items():
        _git(repo, "checkout", "-q", "-b", branch, "main")
        _write(repo, files)
        _commit(repo, branch)
    _git(repo, "checkout", "-q", "main")
    return repo


def integration_breaks(repo: Path, branches: list[str]) -> bool:
    """Merge ``branches`` into a scratch clone and report whether anything uses a missing path.

    The judgement reads the merged spec, every client file and the merged
    deployment's routes; it does not look at any digest or record.
    """
    scratch = Path(tempfile.mkdtemp(prefix=f"{repo.name}-merge-", dir=repo.parent))
    subprocess.run(["git", "clone", "-q", str(repo), str(scratch)], check=True)
    for branch in branches:
        _git(scratch, "merge", "-q", "--no-edit", f"origin/{branch}")
    defined = set(_PATH.findall((scratch / "api" / "spec.yaml").read_text(encoding="utf-8")))
    calls = {
        call
        for path in sorted((scratch / "clients").rglob("*.py"))
        for call in _CALL.findall(path.read_text(encoding="utf-8"))
    }
    served = set(_ROUTE.findall((scratch / "deploy" / "service.txt").read_text(encoding="utf-8")))
    return not (calls | served) <= defined


def run_scenario(root: Path, scenario: Scenario) -> dict[str, object]:
    """Return what the check flagged, then whether the real merge broke."""
    repo = build(root, scenario)
    branches = list(scenario.branches)
    try:
        conflicts = check(repo, "main", branches).conflicts
    except ContractError as exc:
        broke = integration_breaks(repo, branches)
        return {
            "scenario": scenario.name,
            "branches": branches,
            "flagged": [],
            "refused": str(exc),
            "integration_breaks": broke,
            "outcome": "refused",
        }
    broke = integration_breaks(repo, branches)
    flagged = bool(conflicts)
    outcome = {
        (True, True): "caught",
        (False, True): "missed",
        (True, False): "false_block",
        (False, False): "clear",
    }[(flagged, broke)]
    return {
        "scenario": scenario.name,
        "branches": branches,
        "flagged": sorted({conflict.kind for conflict in conflicts}),
        "integration_breaks": broke,
        "outcome": outcome,
    }


def run(results: Path | None = None) -> dict[str, object]:
    """Run every scenario and optionally write the record."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="synapse-contract-conflicts-") as temporary:
        rows = [run_scenario(Path(temporary), scenario) for scenario in SCENARIOS]
    counts = {name: 0 for name in ("caught", "missed", "false_block", "clear", "refused")}
    for row in rows:
        counts[str(row["outcome"])] += 1
    mergeable = sum(1 for row in rows if not row["integration_breaks"])
    summary: dict[str, object] = {
        "schema": "synapse.contract-conflict-benchmark.v1",
        "host": {"python": platform.python_version(), "platform": platform.platform()},
        "policy": "counterfactual: block on any finding (the check itself blocks nothing)",
        "scenarios": rows,
        "counts": counts,
        "lost_parallelism": {
            "blocked_mergeable": counts["false_block"],
            "mergeable": mergeable,
        },
        "seconds": time.perf_counter() - started,
    }
    if results is not None:
        results.parent.mkdir(parents=True, exist_ok=True)
        results.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark from the command line and print the counts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)
    summary = run(args.results)
    print(json.dumps(summary["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

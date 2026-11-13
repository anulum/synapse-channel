# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the integrator contract's surface is pinned and its example runs
"""Pin every command option, Python name and schema ``docs/integrator-contract.md`` uses.

An internal change that leaves these alone cannot break an integrator; a change to
one of them fails here and needs a reviewed edit of the page and this test. The
example adapter runs all six steps against a real in-process hub.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.util
import inspect
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from websockets.asyncio.server import serve

from synapse_channel.cli import build_parser
from synapse_channel.core.authorisation_intent import INTENT_SCHEMA
from synapse_channel.core.execution_outcome import OUTCOME_SCHEMA
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.result_verification_record import SCHEMA as VERIFICATION_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

# Every command line the page shows, as an argument vector the parser must accept.
CONTRACT_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("arm", "install", "--identity", "P/worker", "--start"),
    ("git-claim", "--task-id", "T", "--name", "P/worker", "--paths", "out/report.txt"),
    (
        "operation", "declare", "--task", "T", "--name", "P/worker", "--epoch", "1",
        "--effect", "file", "--path", "out/report.txt", "--sha256", "a" * 64,
    ),
    ("operation", "declare", "--task", "T", "--name", "P/w", "--epoch", "1", "--effect",
     "git_publication", "--operation-id", "op-1"),
    (
        "operation", "declare", "--task", "T", "--name", "P/worker", "--epoch", "1",
        "--effect", "file", "--path", "out/report.txt", "--sha256", "a" * 64,
        "--intent", "intent.json",
    ),
    (
        "operation", "attempt", "--task", "T", "--name", "P/worker", "--operation-id", "op-1",
        "--epoch", "1", "--intent-sha256", "a" * 64,
    ),
    ("operation", "outcome", "--task", "T", "--name", "C/checker", "run-001.json"),
    ("operation", "state", "--task", "T", "--name", "R/recovery", "--operation-id", "op-1"),
    (
        "operation", "seal", "--task", "T", "--name", "R/recovery", "--operation-id", "op-1",
        "--attempt-id", "a" * 32, "--intent-sha256", "a" * 64, "--repository", "repo",
        "--branch", "main", "--base", "b" * 40,
    ),
    (
        "operation", "unmark", "--task", "T", "--name", "R/recovery", "--operation-id", "op-1",
        "--attempt-id", "a" * 32, "--intent-sha256", "a" * 64, "--repository", "repo",
    ),
    ("operation", "verification", "--task", "T", "--name", "C/checker", "verification.json"),
    ("worker-session", "--identity", "P/worker", "--outcome-file", "run-001.json", "--", "x"),
    ("worker-session", "--read-outcome", "run-001.json"),
    (
        "verify-result", "file", "--path", "out/report.txt", "--sha256", "a" * 64,
        "--outcome", "run-001.json",
    ),
)  # fmt: skip

# Every Python name the page and the example's contract steps use, with its kind.
CONTRACT_NAMES: dict[str, dict[str, str]] = {
    "synapse_channel.client.agent": {"SynapseAgent": "class"},
    "synapse_channel.worker_session": {"run_worker_session": "callable"},
    "synapse_channel.worker_outcome": {
        "read_worker_outcome": "callable",
        "OPERATION_ID_ENV": "value",
    },
    "synapse_channel.result_verification": {
        "read_outcome": "callable",
        "verify_file_change": "callable",
    },
    "synapse_channel.core.protocol": {"MessageType": "class"},
    "synapse_channel.core.acl": {
        "CLAIM": "value",
        "MESSAGE": "value",
        "OPERATION_EVIDENCE": "value",
        "AclPolicy": "class",
        "AclRule": "class",
    },
    "synapse_channel.cli": {"main": "callable"},
}

# The required parameters of the contract callables.
CONTRACT_PARAMETERS: dict[str, frozenset[str]] = {
    "synapse_channel.worker_session:run_worker_session": frozenset(
        {"identity", "command", "uri", "outcome_file", "cwd", "arm", "terminal_tmux"}
    ),
    "synapse_channel.worker_outcome:read_worker_outcome": frozenset({"path"}),
    "synapse_channel.result_verification:read_outcome": frozenset({"path"}),
    "synapse_channel.result_verification:verify_file_change": frozenset(
        {"operation_id", "communication"}
    ),
    "synapse_channel.client.agent:SynapseAgent.claim": frozenset({"task_id", "paths"}),
    "synapse_channel.client.agent:SynapseAgent.release": frozenset({"task_id", "epoch"}),
}

# Wire names the example reads or sends.
CONTRACT_MESSAGES = {
    "OPERATION_RECORD": "operation_record",
    "OPERATION_RECORD_RESULT": "operation_record_result",
    "OPERATION_ATTEMPT": "operation_attempt",
    "OPERATION_ATTEMPT_RESULT": "operation_attempt_result",
    "CLAIM_GRANTED": "claim_granted",
    "RELEASE_GRANTED": "release_granted",
}

# Plumbing the self-contained example needs to serve its own hub; an integrator runs
# ``synapse hub`` instead, so these are outside the contract.
DEMO_PLUMBING = {
    "synapse_channel.core.hub": {"SynapseHub"},
    "synapse_channel.core.persistence": {"EventStore"},
}


def _kind(obj: object) -> str:
    if isinstance(obj, type):
        return "class"
    return "callable" if callable(obj) else "value"


@pytest.mark.parametrize("argv", CONTRACT_COMMANDS, ids=lambda argv: " ".join(argv[:2]))
def test_every_command_line_on_the_page_parses(argv: tuple[str, ...]) -> None:
    args = vars(build_parser().parse_args(list(argv)))
    # argparse accepts an unambiguous prefix, so a renamed option would still parse;
    # the destination each pinned option writes to changes with its name.
    options = [token for token in argv if token.startswith("--") and len(token) > 2]
    missing = [token for token in options if token[2:].replace("-", "_") not in args]
    assert not missing, f"options renamed or removed: {missing}"


@pytest.mark.parametrize(
    ("module_path", "name", "kind"),
    [
        (module, name, kind)
        for module, names in CONTRACT_NAMES.items()
        for name, kind in names.items()
    ],
)
def test_every_contract_name_keeps_its_kind(module_path: str, name: str, kind: str) -> None:
    module = importlib.import_module(module_path)
    assert _kind(getattr(module, name)) == kind


@pytest.mark.parametrize(("location", "required"), CONTRACT_PARAMETERS.items())
def test_every_contract_callable_keeps_its_parameters(
    location: str, required: frozenset[str]
) -> None:
    module_path, _, dotted = location.partition(":")
    target: Any = importlib.import_module(module_path)
    for part in dotted.split("."):
        target = getattr(target, part)
    assert required <= set(inspect.signature(target).parameters)


def test_the_wire_names_and_schemas_stay() -> None:
    assert {name: getattr(MessageType, name) for name in CONTRACT_MESSAGES} == CONTRACT_MESSAGES
    assert (OUTCOME_SCHEMA, VERIFICATION_SCHEMA, INTENT_SCHEMA) == (
        "synapse.execution-outcome.v1",
        "synapse.result-verification.v1",
        "synapse.authorisation-intent.v0",
    )


def _imports(path: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("synapse_channel"):
            found.setdefault(str(node.module), set()).update(alias.name for alias in node.names)
    return found


@pytest.mark.parametrize("name", ["integrator_adapter.py", "integrator_provider.py"])
def test_the_example_imports_only_contract_names(name: str) -> None:
    allowed = {module: set(names) for module, names in CONTRACT_NAMES.items()}
    for module, names in DEMO_PLUMBING.items():
        allowed.setdefault(module, set()).update(names)
    for module, names in _imports(EXAMPLES / name).items():
        assert names <= allowed.get(module, set()), f"{name} imports {module}.{names}"


def test_the_page_names_every_contract_command_and_schema() -> None:
    page = (ROOT / "docs" / "integrator-contract.md").read_text(encoding="utf-8")
    for argv in CONTRACT_COMMANDS:
        assert f"synapse {argv[0]}" in page
    for text in (
        "operation-evidence",
        "operation-seal",
        "refs/synapse/attempts/",
        "automatic_retry: false",
        OUTCOME_SCHEMA,
        VERIFICATION_SCHEMA,
        INTENT_SCHEMA,
        "--intent",
        "--intent-sha256",
        "approved_fresh",
    ):
        assert text in page


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


async def test_the_example_adapter_runs_all_six_steps(tmp_path: Path) -> None:
    adapter = _load("integrator_adapter")
    async with adapter.demo_hub(tmp_path) as uri:
        run = await adapter.run_adapter(uri, tmp_path)
    assert run.provider_exit == 0
    assert run.outcome_state == run.inquiry_state == "confirmed_done"
    assert (run.outcome_record, run.verification_record) == ("accepted", "accepted")
    assert run.verification_result == "verified"
    assert run.released is True
    assert (tmp_path / "artefact.txt").read_text(encoding="utf-8") == "hello, contract\n"


async def test_the_example_main_prints_success(capsys: pytest.CaptureFixture[str]) -> None:
    adapter = _load("integrator_adapter")
    assert await adapter._main() == 0
    assert capsys.readouterr().out.strip().endswith("success: integrator adapter completed")


async def test_the_provider_refuses_without_an_operation_id_or_a_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _load("integrator_provider")
    adapter = _load("integrator_adapter")
    argv = ["--identity", "DEMO/worker", "--task", "DEMO/none", "--epoch", "1"]
    argv += ["--path", str(tmp_path / "x.txt"), "--text", "x"]
    monkeypatch.delenv("SYN_OPERATION_ID", raising=False)
    assert provider.run(["--uri", "ws://127.0.0.1:1", *argv]) == 5
    monkeypatch.setitem(os.environ, "SYN_OPERATION_ID", "op-unclaimed")
    async with adapter.demo_hub(tmp_path) as uri:
        refused = await asyncio.to_thread(provider.run, ["--uri", uri, *argv])
    assert refused == 4
    assert not (tmp_path / "x.txt").exists()


async def test_the_adapter_refuses_a_hub_that_never_welcomes(tmp_path: Path) -> None:
    adapter = _load("integrator_adapter")

    async def _drop(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(_drop, "127.0.0.1", 0)
    sockets = server.sockets
    assert sockets is not None
    port = sockets[0].getsockname()[1]
    try:
        with pytest.raises(RuntimeError, match="could not connect"):
            await adapter.run_adapter(f"ws://127.0.0.1:{port}", tmp_path)
    finally:
        server.close()
        await server.wait_closed()


async def test_the_adapter_stops_when_the_claim_is_never_granted(tmp_path: Path) -> None:
    adapter = _load("integrator_adapter")

    async def _welcome_and_hold(connection: Any) -> None:
        await connection.send('{"type": "welcome"}')
        await connection.wait_closed()

    async with serve(_welcome_and_hold, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(TimeoutError, match="no claim_granted frame arrived"):
            await adapter.run_adapter(f"ws://127.0.0.1:{port}", tmp_path)


async def test_the_demo_hub_fails_when_it_never_becomes_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hub that does not bind hands out no address and still releases its checkpoint."""
    adapter = _load("integrator_adapter")
    closed: list[object] = []
    release = adapter.SynapseHub.close

    async def _never_bind(self: object, host: str, port: int, **_kwargs: object) -> None:
        del self, host, port
        await asyncio.Event().wait()

    def _close(self: object) -> None:
        closed.append(self)
        release(self)

    monkeypatch.setattr(adapter.SynapseHub, "serve", _never_bind)
    monkeypatch.setattr(adapter.SynapseHub, "close", _close)
    with pytest.raises(TimeoutError):
        await adapter.demo_hub(tmp_path).__aenter__()
    assert len(closed) == 1


async def test_the_example_main_stops_when_the_hub_refuses_evidence(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _load("integrator_adapter")
    original = adapter.demo_policy

    def _no_evidence() -> object:
        policy = original()
        kept = [rule for rule in policy.rules if rule.permission != adapter.OPERATION_EVIDENCE]
        return adapter.AclPolicy(kept)

    monkeypatch.setattr(adapter, "demo_policy", _no_evidence)
    assert await adapter._main() == 1
    report = capsys.readouterr().out
    assert "integrator adapter stopped:" in report
    assert "refused" in report


def test_the_example_program_completes_in_a_child_process() -> None:
    env = os.environ.copy()
    src = str(ROOT / "src")
    previous = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not previous else src + os.pathsep + previous
    completed = subprocess.run(
        [sys.executable, str(EXAMPLES / "integrator_adapter.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith("success: integrator adapter completed")

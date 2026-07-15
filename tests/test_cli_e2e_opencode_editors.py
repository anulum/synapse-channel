# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pinned real editor clients against OpenCode ACP
"""Run one explicitly selected, fail-closed editor-to-OpenCode ACP turn."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tools.opencode_compatibility_contract import load_compatibility
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.opencode_compatibility_contract import load_compatibility

from cli_e2e_helpers import git_repo, isolated_hub, run_cli
from e2e.opencode_editors.governance_contract import (
    PROMPT,
    RESPONSE,
    assert_durable_governance,
    assert_provider_governance,
    enqueue_governance_turn,
    source_environment,
    synapse_launcher,
)
from e2e.opencode_editors.jetbrains_timing import DEFAULT_JETBRAINS_TIMING
from e2e.opencode_editors.trace_contract import assert_editor_trace
from fixtures.opencode.llm import ScriptedLlmServer
from fixtures.opencode.process import OPENCODE_VERSION, TEST_MODEL, isolated_environment

_CLIENT_TIMEOUT_SECONDS = {
    "emacs": 180,
    "jetbrains": DEFAULT_JETBRAINS_TIMING.parent_timeout_seconds,
    "neovim": 180,
    "zed": 180,
}
_ISOLATED_HUB_READY_TIMEOUT_SECONDS = 60.0


def _expected_config_after_adapter_uninstall(
    installed: object,
) -> dict[str, object]:
    """Remove exactly the Synapse-owned MCP entry from a parsed config."""
    if not isinstance(installed, dict):
        raise ValueError("installed OpenCode config must be an object")
    expected: dict[str, object] = deepcopy(installed)
    mcp = expected.get("mcp")
    if not isinstance(mcp, dict):
        raise ValueError("installed OpenCode config omitted its MCP object")
    entry = mcp.get("synapse")
    if not isinstance(entry, dict):
        raise ValueError("installed OpenCode config omitted its Synapse MCP entry")
    environment = entry.get("environment")
    if (
        not isinstance(environment, dict)
        or environment.get("SYNAPSE_ADAPTER_OWNER") != "synapse-channel"
    ):
        raise ValueError("installed OpenCode config lost its Synapse owner marker")
    del mcp["synapse"]
    if not mcp:
        del expected["mcp"]
    return expected


def test_expected_config_after_uninstall_preserves_every_nonowned_entry() -> None:
    installed = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {"write": "allow"},
        "mcp": {
            "other": {"type": "local"},
            "synapse": {"environment": {"SYNAPSE_ADAPTER_OWNER": "synapse-channel"}},
        },
    }

    assert _expected_config_after_adapter_uninstall(installed) == {
        "$schema": "https://opencode.ai/config.json",
        "permission": {"write": "allow"},
        "mcp": {"other": {"type": "local"}},
    }
    assert "synapse" in installed["mcp"]


@pytest.mark.parametrize(
    ("installed", "message"),
    [
        ([], "must be an object"),
        ({}, "omitted its MCP object"),
        ({"mcp": {}}, "omitted its Synapse MCP entry"),
        (
            {"mcp": {"synapse": {"environment": {}}}},
            "lost its Synapse owner marker",
        ),
    ],
)
def test_expected_config_after_uninstall_rejects_unowned_or_invalid_state(
    installed: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _expected_config_after_adapter_uninstall(installed)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise AssertionError(f"selected editor acceptance requires {name}")
    return value


def _executable(name: str) -> Path:
    path = Path(_required_env(name)).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise AssertionError(f"{name} is not an executable regular file: {path}")
    return path


def _exact_opencode() -> Path:
    binary = _executable("OPENCODE_BIN")
    completed = subprocess.run(  # nosec B603
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0 or completed.stdout.strip() != OPENCODE_VERSION:
        raise AssertionError(
            f"editor acceptance requires OpenCode {OPENCODE_VERSION}, got "
            f"{completed.stdout.strip() or completed.stderr.strip() or 'no version'}"
        )
    return binary


def _command(client: str, fixture_dir: Path) -> Sequence[str]:
    if client == "neovim":
        return [
            _executable("SYNAPSE_NVIM_BIN").as_posix(),
            "--headless",
            "-u",
            "NONE",
            "-l",
            str(fixture_dir / "neovim_client.lua"),
        ]
    if client == "emacs":
        command = [_executable("SYNAPSE_EMACS_BIN").as_posix(), "--batch", "-Q"]
        for variable in (
            "SYNAPSE_SHELL_MAKER_DIR",
            "SYNAPSE_ACP_EL_DIR",
            "SYNAPSE_AGENT_SHELL_DIR",
        ):
            command.extend(("-L", _required_env(variable)))
        command.extend(("-l", str(fixture_dir / "emacs_client.el")))
        return command
    if client == "zed":
        _executable("SYNAPSE_ZED_BIN")
        return [sys.executable, str(fixture_dir / "zed_client.py")]
    if client == "jetbrains":
        _executable("SYNAPSE_JETBRAINS_BIN")
        Path(_required_env("SYNAPSE_JETBRAINS_PLUGINS")).resolve(strict=True)
        return [sys.executable, str(fixture_dir / "jetbrains_client.py")]
    raise AssertionError(f"unsupported editor acceptance client: {client}")


def _expected_clients(client: str) -> dict[str, str]:
    """Return the selected editor's exact ACP wire identity."""
    identity = load_compatibility().client(client)
    return {identity.name: identity.version}


def test_real_editor_client_enforces_synapse_governance(tmp_path: Path) -> None:
    client = os.environ.get("SYNAPSE_EDITOR_E2E_CLIENT", "").strip().lower()
    if not client:
        pytest.skip("dedicated editor acceptance workflow selects the real client")
    if client not in _CLIENT_TIMEOUT_SECONDS:
        raise AssertionError(f"unknown SYNAPSE_EDITOR_E2E_CLIENT: {client}")

    fixture_dir = Path(__file__).resolve().parent / "e2e" / "opencode_editors"
    artifact_dir = Path(_required_env("SYNAPSE_EDITOR_E2E_ARTIFACT_DIR")).resolve()
    artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    trace = artifact_dir / f"{client}-acp.jsonl"
    home = tmp_path / "home"
    project = git_repo(tmp_path / "project")
    home.mkdir(mode=0o700)
    project.chmod(0o700)
    opencode = _exact_opencode()
    identity = f"editor/{client}"
    launcher = synapse_launcher(tmp_path / "synapse-current")
    config_path = project / ".opencode" / "opencode.json"
    config_path.parent.mkdir(mode=0o700)
    config_path.write_text(
        json.dumps({"permission": {"write": "allow"}}) + "\n",
        encoding="utf-8",
    )
    config_path.chmod(0o600)

    with (
        ScriptedLlmServer() as llm,
        isolated_hub(
            tmp_path,
            ready_timeout=_ISOLATED_HUB_READY_TIMEOUT_SECONDS,
        ) as hub,
    ):
        try:
            installed = run_cli(
                "adapters",
                "opencode",
                "install",
                "--identity",
                identity,
                "--project",
                str(project),
                "--synapse-bin",
                str(launcher),
                uri=hub.uri,
                cwd=project,
            )
            assert installed.ok(), installed.output
            environment = source_environment(
                isolated_environment(
                    home,
                    llm.url,
                    pure=False,
                    disable_project_config=False,
                )
            )
            config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
            config["model"] = TEST_MODEL
            environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
            proxy_argv = [
                sys.executable,
                str(fixture_dir / "acp_trace_proxy.py"),
                "--trace",
                str(trace),
                "--opencode-bin",
                str(opencode),
                "--cwd",
                str(project),
            ]
            environment.update(
                {
                    "SYNAPSE_ACP_PROXY_ARGV_JSON": json.dumps(proxy_argv),
                    "SYNAPSE_ACP_TRACE": str(trace),
                    "SYNAPSE_EDITOR_E2E_PROJECT": str(project),
                    "SYNAPSE_EDITOR_E2E_PROMPT": PROMPT,
                    "SYNAPSE_EDITOR_E2E_RESPONSE": RESPONSE,
                }
            )
            enqueue_governance_turn(llm, project)
            command = _command(client, fixture_dir)
            completed = subprocess.run(  # nosec B603
                command,
                cwd=project,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=_CLIENT_TIMEOUT_SECONDS[client],
            )
            assert completed.returncode == 0, (
                f"{client} acceptance exited {completed.returncode}\n"
                f"stdout:\n{completed.stdout[-8000:]}\n"
                f"stderr:\n{completed.stderr[-12000:]}"
            )
            assert_editor_trace(
                trace,
                expected_clients=_expected_clients(client),
                expected_agent_version=OPENCODE_VERSION,
                prompt=PROMPT,
            )
            assert_provider_governance(llm.prompt_requests)
            assert (project / "allowed.txt").read_text(encoding="utf-8") == "governed\n"
            assert not (project / "denied-before.txt").exists()
            assert not (project / "denied-after.txt").exists()
        finally:
            cleanup_errors: list[str] = []
            expected_config: dict[str, object] | None = None
            try:
                installed_config = json.loads(config_path.read_text(encoding="utf-8"))
                expected_config = _expected_config_after_adapter_uninstall(installed_config)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                cleanup_errors.append(
                    f"installed OpenCode configuration could not be verified: {exc}"
                )
            removed = run_cli(
                "adapters",
                "opencode",
                "uninstall",
                "--project",
                str(project),
                cwd=project,
            )
            if not removed.ok():
                cleanup_errors.append(f"OpenCode adapter uninstall failed: {removed.output}")
            try:
                restored_config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                cleanup_errors.append(f"OpenCode configuration could not be verified: {exc}")
            else:
                if expected_config is not None and restored_config != expected_config:
                    cleanup_errors.append(
                        "OpenCode adapter uninstall changed non-owned configuration"
                    )
                if restored_config.get("permission") != {"write": "allow"}:
                    cleanup_errors.append(
                        "OpenCode adapter uninstall lost the original write permission"
                    )
            if cleanup_errors:
                detail = "; ".join(cleanup_errors)
                active_error = sys.exc_info()[1]
                if active_error is not None:
                    raise AssertionError(
                        f"editor acceptance and cleanup both failed: {detail}"
                    ) from active_error
                else:
                    raise AssertionError(detail)

    assert_durable_governance(hub.db_path, project, identity)

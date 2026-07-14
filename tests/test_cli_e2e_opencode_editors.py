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
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from e2e.opencode_editors.trace_contract import assert_editor_trace
from fixtures.opencode.llm import ScriptedLlmServer
from fixtures.opencode.process import OPENCODE_VERSION, TEST_MODEL, isolated_environment

_CLIENT_NAMES = {
    "emacs": ("agent-shell",),
    "neovim": ("CodeCompanion.nvim",),
    "zed": ("zed",),
}
_PROMPT = "Reply with the deterministic SYNAPSE editor acceptance token."
_RESPONSE = "SYNAPSE_EDITOR_E2E_RESPONSE"


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


def _expected_client_names(client: str) -> tuple[str, ...]:
    if client != "jetbrains":
        return _CLIENT_NAMES[client]
    return (_required_env("SYNAPSE_JETBRAINS_CLIENT_NAME"),)


def _assert_provider_request(requests: Sequence[Mapping[str, object]]) -> None:
    if len(requests) != 1:
        raise AssertionError(f"expected one editor provider request, received {len(requests)}")
    request = requests[0]
    if request.get("model") != "test-model":
        raise AssertionError(f"editor used an unexpected model: {request.get('model')!r}")
    if _PROMPT not in json.dumps(request, sort_keys=True):
        raise AssertionError("editor provider request omitted the acceptance prompt")


def test_real_editor_client_completes_opencode_acp_turn(tmp_path: Path) -> None:
    client = os.environ.get("SYNAPSE_EDITOR_E2E_CLIENT", "").strip().lower()
    if not client:
        pytest.skip("dedicated editor acceptance workflow selects the real client")
    if client not in {*_CLIENT_NAMES, "jetbrains"}:
        raise AssertionError(f"unknown SYNAPSE_EDITOR_E2E_CLIENT: {client}")

    fixture_dir = Path(__file__).resolve().parent / "e2e" / "opencode_editors"
    artifact_dir = Path(_required_env("SYNAPSE_EDITOR_E2E_ARTIFACT_DIR")).resolve()
    artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    trace = artifact_dir / f"{client}-acp.jsonl"
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir(mode=0o700)
    project.mkdir(mode=0o700)
    opencode = _exact_opencode()

    with ScriptedLlmServer() as llm:
        environment = isolated_environment(
            home,
            llm.url,
            pure=True,
            disable_project_config=True,
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
                "SYNAPSE_EDITOR_E2E_PROMPT": _PROMPT,
                "SYNAPSE_EDITOR_E2E_RESPONSE": _RESPONSE,
            }
        )
        llm.enqueue_text(_RESPONSE)
        command = _command(client, fixture_dir)
        completed = subprocess.run(  # nosec B603
            command,
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert completed.returncode == 0, (
            f"{client} acceptance exited {completed.returncode}\n"
            f"stdout:\n{completed.stdout[-8000:]}\n"
            f"stderr:\n{completed.stderr[-12000:]}"
        )
        assert_editor_trace(
            trace,
            expected_client_names=_expected_client_names(client),
            prompt=_PROMPT,
        )
        _assert_provider_request(llm.prompt_requests)

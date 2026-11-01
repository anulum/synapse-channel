# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real OpenCode CLI, server, plugin, and ACP acceptance

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from cli_e2e_helpers import git_repo, isolated_hub, run_cli
from fixtures.opencode.acp import acp_session_prompt
from fixtures.opencode.runtime import (
    TEST_MODEL,
    ScriptedLlmServer,
    acp_initialize,
    find_opencode,
    governed_project_environment,
    isolated_environment,
    run_opencode,
    running_opencode_server,
)
from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.opencode_api import OpenCodeApiParticipant
from synapse_channel.participants.opencode_stream import parse_opencode_stream


def _source_environment(environment: dict[str, str]) -> dict[str, str]:
    source = str(Path(__file__).resolve().parents[1] / "src")
    inherited = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = source if not inherited else source + os.pathsep + inherited
    return environment


def _synapse_launcher(path: Path) -> Path:
    path.write_text(
        f"#!{sys.executable}\nfrom synapse_channel.cli import main\nraise SystemExit(main())\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def _assert_success(returncode: int, stdout: str, stderr: str) -> None:
    assert returncode == 0, f"stdout:\n{stdout[-2000:]}\nstderr:\n{stderr[-4000:]}"


def _attached_stdout_is_the_server_turn(stdout: str, answer: str) -> None:
    """Accept an early return or the drained 1.18.32 JSON turn, and nothing else."""
    if not stdout:
        return
    outcome = parse_opencode_stream(stdout.splitlines())
    assert outcome.is_error is False
    assert outcome.subtype == "success"
    assert outcome.answer == answer


def _ci_event(event_type: str, part: dict[str, object]) -> dict[str, object]:
    """One Linux CI attach event. The session is the hosted 3.12 shard value."""
    session = "ses_e6b9b7003ffe0d3hH2GGlgol75"
    return {
        "type": event_type,
        "timestamp": 1793490982137,
        "sessionID": session,
        "part": {"sessionID": session, **part},
    }


_CI_DRAINED_ATTACH_STREAM = (
    "\n".join(
        json.dumps(event)
        for event in (
            _ci_event(
                "step_start",
                {
                    "id": "prt_1946494b9001DyhjmLsvrg6N2o",
                    "messageID": "msg_1946492ef001Q87jjkB4Qi2x9w",
                    "type": "step-start",
                },
            ),
            _ci_event(
                "text",
                {
                    "id": "prt_1946494bd0017nbXDRjvC4RWXp",
                    "messageID": "msg_1946492ef001Q87jjkB4Qi2x9w",
                    "type": "text",
                    "text": "attached server response",
                    "time": {"start": 1793490982077, "end": 1793490982081},
                },
            ),
            _ci_event(
                "step_finish",
                {
                    "id": "prt_1946494c6001i5b8NHPkZtZx1d",
                    "reason": "stop",
                    "messageID": "msg_1946492ef001Q87jjkB4Qi2x9w",
                    "type": "step-finish",
                    "tokens": {
                        "total": 3,
                        "input": 2,
                        "output": 1,
                        "reasoning": 0,
                        "cache": {"write": 0, "read": 0},
                    },
                    "cost": 0,
                },
            ),
        )
    )
    + "\n"
)


def test_drained_attach_stream_is_the_enqueued_answer() -> None:
    """The Linux CI attach stream is the server turn, and noise still fails."""
    assert _CI_DRAINED_ATTACH_STREAM != ""
    _attached_stdout_is_the_server_turn(_CI_DRAINED_ATTACH_STREAM, "attached server response")
    _attached_stdout_is_the_server_turn("", "attached server response")
    try:
        _attached_stdout_is_the_server_turn('{"type":"error"}\n', "attached server response")
    except AssertionError:
        return
    raise AssertionError("a drifted attach stream was accepted")


def test_real_cli_jsonl_emitter_and_acp_handshake(tmp_path: Path) -> None:
    binary = find_opencode()
    home = tmp_path / "home"
    home.mkdir()
    with ScriptedLlmServer() as llm:
        environment = isolated_environment(home, llm.url, pure=True, disable_project_config=True)
        llm.enqueue_text("real OpenCode JSONL")
        completed = run_opencode(
            binary,
            [
                "run",
                "--model",
                TEST_MODEL,
                "--format",
                "json",
                "--dir",
                str(tmp_path),
                "emit a deterministic response",
            ],
            cwd=tmp_path,
            env=environment,
        )
        _assert_success(completed.returncode, completed.stdout, completed.stderr)
        outcome = parse_opencode_stream(completed.stdout.splitlines())
        assert outcome.is_error is False
        assert outcome.answer == "real OpenCode JSONL"
        assert outcome.session_id
        assert [request["model"] for request in llm.prompt_requests] == ["test-model"]

        response, stderr = acp_initialize(binary, cwd=tmp_path, env=environment)
        assert stderr == ""
        assert response["id"] == 1
        result = response["result"]
        assert result["protocolVersion"] == 1
        assert result["agentInfo"]["name"] == "OpenCode"
        assert result["agentCapabilities"]["mcpCapabilities"] == {
            "http": True,
            "sse": True,
        }
        assert result["authMethods"][0]["_meta"]["terminal-auth"]


def test_real_acp_session_and_scripted_provider_prompt(tmp_path: Path) -> None:
    binary = find_opencode()
    home = tmp_path / "home"
    home.mkdir()
    with ScriptedLlmServer() as llm:
        environment = isolated_environment(home, llm.url, pure=True, disable_project_config=True)
        configuration = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
        configuration["model"] = TEST_MODEL
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(configuration)
        llm.enqueue_text("ACP session response")

        session_id, stop_reason, rendered, stderr = acp_session_prompt(
            binary,
            cwd=tmp_path,
            env=environment,
            prompt="Return the deterministic response",
        )
        assert session_id
        assert stop_reason == "end_turn"
        assert rendered == "ACP session response"
        assert stderr == ""
        assert len(llm.prompt_requests) == 1
        assert llm.prompt_requests[0]["model"] == "test-model"


def test_real_authenticated_server_attach_and_direct_api(tmp_path: Path) -> None:
    binary = find_opencode()
    home = tmp_path / "home"
    home.mkdir()
    username = "opencode-test"
    password = "server-password"
    password_file = tmp_path / "server.password"
    password_file.write_text(password + "\n", encoding="utf-8")
    password_file.chmod(0o600)

    with ScriptedLlmServer() as llm:
        environment = isolated_environment(home, llm.url, pure=True, disable_project_config=True)
        with running_opencode_server(
            binary,
            cwd=tmp_path,
            env=environment,
            username=username,
            password=password,
        ) as server:
            unauthenticated = OpenCodeApiParticipant(
                "seat/unauthenticated", directory=tmp_path, endpoint=server.url
            )
            assert unauthenticated.health().available is False

            llm.enqueue_text("attached server response")
            attached_environment = {
                **environment,
                "OPENCODE_SERVER_USERNAME": username,
                "OPENCODE_SERVER_PASSWORD": password,
            }
            attached = run_opencode(
                binary,
                [
                    "run",
                    "--attach",
                    server.url,
                    "--model",
                    TEST_MODEL,
                    "--format",
                    "json",
                    "--dir",
                    str(tmp_path),
                    "exercise attach mode",
                ],
                cwd=tmp_path,
                env=attached_environment,
            )
            _assert_success(attached.returncode, attached.stdout, attached.stderr)
            # OpenCode 1.18.32 sometimes returns after the prompt POST, before it
            # drains the event stream. When it does drain, stdout is that JSON turn.
            _attached_stdout_is_the_server_turn(attached.stdout, "attached server response")
            assert len(llm.prompt_requests) == 1
            assert "exercise attach mode" in json.dumps(llm.prompt_requests[0])
            assert password not in attached.stdout + attached.stderr

            llm.enqueue_text("direct API response")
            api = OpenCodeApiParticipant(
                "seat/api",
                directory=tmp_path,
                model=TEST_MODEL,
                endpoint=server.url,
                username=username,
                password_file=str(password_file),
            )
            result = api.run_turn(TurnRequest("opencode", "exercise direct API"))
            assert result["is_error"] is False
            assert result["answer"] == "direct API response"
            assert result["session"]
            assert len(llm.prompt_requests) == 2


def test_real_native_plugin_live_claim_and_adapter_lifecycle(tmp_path: Path) -> None:
    binary = find_opencode()
    repo = git_repo(tmp_path / "repo")
    repo.chmod(0o700)
    home = tmp_path / "home"
    home.mkdir()
    config = repo / ".opencode" / "opencode.json"
    config.parent.mkdir(mode=0o700)
    config.write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "permission": {"write": "allow"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    launcher = _synapse_launcher(tmp_path / "synapse-current")
    allowed_path = repo / "allowed.txt"
    whitespace_path = repo / "allowed.txt "
    denied_path = repo / "denied.txt"

    with ScriptedLlmServer() as llm, isolated_hub(tmp_path) as hub:
        environment = _source_environment(
            isolated_environment(home, llm.url, pure=False, disable_project_config=True)
        )
        identity_environment = {"XDG_DATA_HOME": environment["XDG_DATA_HOME"]}
        installed = run_cli(
            "adapters",
            "opencode",
            "install",
            "--identity",
            "seat/one",
            "--project",
            str(repo),
            "--synapse-bin",
            str(launcher),
            uri=hub.uri,
            cwd=repo,
            env=identity_environment,
        )
        assert installed.ok(), installed.output
        status = run_cli(
            "adapters",
            "opencode",
            "status",
            "--project",
            str(repo),
            cwd=repo,
            env=identity_environment,
        )
        assert status.ok(), status.output

        claimed = run_cli(
            "git-claim",
            "OPENCODE-E2E",
            "--paths",
            allowed_path.name,
            "--auto-release-on",
            "manual",
            "--name",
            "seat/one",
            uri=hub.uri,
            cwd=repo,
            env=identity_environment,
        )
        assert claimed.ok(), claimed.output

        environment = governed_project_environment(environment, repo)
        llm.enqueue_tool("write", {"filePath": str(allowed_path), "content": "allowed\n"})
        llm.enqueue_text("allowed continuation")
        allowed = run_opencode(
            binary,
            [
                "run",
                "--model",
                TEST_MODEL,
                "--format",
                "json",
                "--dir",
                str(repo),
                "write the claimed file",
            ],
            cwd=repo,
            env=environment,
        )
        _assert_success(allowed.returncode, allowed.stdout, allowed.stderr)
        assert allowed_path.read_text(encoding="utf-8") == "allowed\n"

        llm.enqueue_tool("write", {"filePath": str(whitespace_path), "content": "bypass\n"})
        llm.enqueue_text("whitespace continuation")
        whitespace = run_opencode(
            binary,
            [
                "run",
                "--model",
                TEST_MODEL,
                "--format",
                "json",
                "--dir",
                str(repo),
                "try a whitespace-bearing path outside the claim",
            ],
            cwd=repo,
            env=environment,
        )
        _assert_success(whitespace.returncode, whitespace.stdout, whitespace.stderr)
        assert not whitespace_path.exists()
        assert "claim" in whitespace.stdout.lower()

        llm.enqueue_tool("write", {"filePath": str(denied_path), "content": "denied\n"})
        llm.enqueue_text("denied continuation")
        denied = run_opencode(
            binary,
            [
                "run",
                "--model",
                TEST_MODEL,
                "--format",
                "json",
                "--dir",
                str(repo),
                "try the unclaimed file",
            ],
            cwd=repo,
            env=environment,
        )
        _assert_success(denied.returncode, denied.stdout, denied.stderr)
        assert not denied_path.exists()
        assert "claim" in denied.stdout.lower()

        upgraded = run_cli(
            "adapters",
            "opencode",
            "install",
            "--identity",
            "seat/two",
            "--project",
            str(repo),
            "--synapse-bin",
            str(launcher),
            uri=hub.uri,
            cwd=repo,
            env=identity_environment,
        )
        assert upgraded.ok(), upgraded.output
        upgraded_config = json.loads(config.read_text(encoding="utf-8"))
        command = upgraded_config["mcp"]["synapse"]["command"]
        assert command[command.index("--name") + 1] == "seat/two"

        removed = run_cli(
            "adapters",
            "opencode",
            "uninstall",
            "--project",
            str(repo),
            cwd=repo,
            env=identity_environment,
        )
        assert removed.ok(), removed.output
        final_config = json.loads(config.read_text(encoding="utf-8"))
        assert final_config == {
            "$schema": "https://opencode.ai/config.json",
            "permission": {"write": "allow"},
        }
        assert not (repo / ".opencode" / "plugins" / "synapse-claim-guard.js").exists()

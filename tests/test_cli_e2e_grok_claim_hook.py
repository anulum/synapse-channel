# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real CLI/hub journey for the Grok search_replace/write claim guard

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from cli_e2e_helpers import CliResult, free_port, git_repo, isolated_hub, run_cli

# Subprocess CLI journeys may chdir into a temp git repo; keep the package importable
# via an absolute PYTHONPATH so relative ``src`` never resolves against the temp cwd.
# Scope that PYTHONPATH to ``_CLI_ENV`` (which every journey below passes as ``env=``);
# do NOT mutate the process-wide ``os.environ``. A module-level
# ``os.environ["PYTHONPATH"] = ...`` leaks the src path into every later subprocess in
# the whole pytest run — it poisoned the installed-wheel console-script gate, whose
# checker then imported ``synapse_channel`` from src instead of the built wheel.
_SRC = str((Path(__file__).resolve().parents[1] / "src").resolve())
_PRIOR = os.environ.get("PYTHONPATH", "")
_PYTHONPATH = _SRC + (os.pathsep + _PRIOR if _PRIOR else "")
_CLI_ENV: dict[str, str] = {**os.environ, "PYTHONPATH": _PYTHONPATH}


def _run(
    *args: str,
    uri: str | None = None,
    timeout: float = 20.0,
    stdin: str | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> CliResult:
    """Run the packaged CLI with an absolute PYTHONPATH for this worktree."""
    child_env = dict(_CLI_ENV)
    if env is not None:
        child_env.update(env)
    return run_cli(
        *args,
        uri=uri,
        timeout=timeout,
        stdin=stdin,
        cwd=cwd,
        env=child_env,
    )


def _event(repo: Path, target: Path, *, tool: str = "search_replace") -> str:
    """Build a Grok-native camelCase PreToolUse event for one file mutation."""
    tool_input: dict[str, str]
    if tool == "write":
        tool_input = {"path": str(target), "contents": "content\n"}
    else:
        tool_input = {
            "path": str(target),
            "old_string": "a",
            "new_string": "b",
        }
    return json.dumps(
        {
            "sessionId": "e2e-session",
            "toolUseId": f"tool-{target.name}",
            "cwd": str(repo),
            "hookEventName": "PreToolUse",
            "toolName": tool,
            "toolInput": tool_input,
        }
    )


def test_live_claim_allows_owned_file_and_denies_unclaimed_file(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    (repo / "src").mkdir()
    with isolated_hub(tmp_path) as hub:
        claimed = _run(
            "git-claim",
            "GROK-E2E",
            "--paths",
            "src/owned.py",
            "--auto-release-on",
            "manual",
            "--name",
            "seat/one",
            uri=hub.uri,
            cwd=repo,
        )
        assert claimed.ok(), claimed.output

        allowed = _run(
            "adapters",
            "grok-claim-hook",
            "--identity",
            "seat/one",
            stdin=_event(repo, repo / "src" / "owned.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert allowed.ok(), allowed.output
        assert allowed.stdout == ""

        denied = _run(
            "adapters",
            "grok-claim-hook",
            "--identity",
            "seat/one",
            stdin=_event(repo, repo / "src" / "other.py", tool="write"),
            uri=hub.uri,
            cwd=repo,
        )
        assert denied.ok(), denied.output
        output = json.loads(denied.stdout)
        assert output["decision"] == "deny"
        assert "permissionDecision" not in output

        wrong_owner = _run(
            "adapters",
            "grok-claim-hook",
            "--identity",
            "seat/two",
            stdin=_event(repo, repo / "src" / "owned.py"),
            uri=hub.uri,
            cwd=repo,
        )
        assert wrong_owner.ok(), wrong_owner.output
        wrong_owner_output = json.loads(wrong_owner.stdout)
        assert wrong_owner_output["decision"] == "deny"

        malformed = _run(
            "adapters",
            "grok-claim-hook",
            "--identity",
            "seat/one",
            stdin="{not-json",
            uri=hub.uri,
            cwd=repo,
        )
        assert malformed.ok(), malformed.output
        malformed_output = json.loads(malformed.stdout)
        assert malformed_output["decision"] == "deny"


def test_printed_token_file_recipe_authenticates_secured_hub(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    token_file = tmp_path / "hub.token"
    token_file.write_text("secured-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    with isolated_hub(tmp_path, extra_args=("--token-file", str(token_file))) as hub:
        claimed = _run(
            "git-claim",
            "GROK-TOKEN-E2E",
            "--paths",
            "README.md",
            "--auto-release-on",
            "manual",
            "--name",
            "seat/one",
            "--token-file",
            str(token_file),
            uri=hub.uri,
            cwd=repo,
        )
        assert claimed.ok(), claimed.output

        rendered = _run(
            "adapters",
            "grok-claim-hook",
            "--identity",
            "seat/one",
            "--token-file",
            str(token_file),
            "--print-config",
            "--synapse-bin",
            sys.executable,
            uri=hub.uri,
            cwd=repo,
        )
        assert rendered.ok(), rendered.output
        hook = json.loads(rendered.stdout)["hooks"]["PreToolUse"][0]["hooks"][0]
        command = hook["command"]
        assert "grok-claim-hook" in command
        assert str(token_file.resolve()) in command
        assert "secured-token" not in command
        # Grok recipes are a single shell command string (not Claude's exec+args form).
        # Exercise the same flags the recipe encodes against the secured hub.
        allowed = _run(
            "adapters",
            "grok-claim-hook",
            "--identity",
            "seat/one",
            "--token-file",
            str(token_file),
            stdin=_event(repo, repo / "README.md"),
            uri=hub.uri,
            cwd=repo,
        )
        assert allowed.ok(), allowed.output
        assert allowed.stdout == ""


def test_unreachable_hub_denies_on_exit_zero(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    unused_port = free_port()
    result = _run(
        "adapters",
        "grok-claim-hook",
        "--identity",
        "seat/one",
        "--ready-timeout",
        "0.05",
        stdin=_event(repo, repo / "README.md"),
        uri=f"ws://127.0.0.1:{unused_port}",
        cwd=repo,
    )
    assert result.ok(), result.output
    output = json.loads(result.stdout)
    assert output["decision"] == "deny"
    assert output["reason"]


def test_cli_prints_mergeable_config_without_writing_settings(tmp_path: Path) -> None:
    result = _run(
        "adapters",
        "grok-claim-hook",
        "--identity",
        "seat/one",
        "--print-config",
        "--synapse-bin",
        sys.executable,
        cwd=tmp_path,
    )
    assert result.ok(), result.output
    config = json.loads(result.stdout)
    matcher = config["hooks"]["PreToolUse"][0]["matcher"]
    assert "search_replace" in matcher and "write" in matcher
    assert "run_terminal_command" in matcher
    assert not (tmp_path / ".grok").exists()


def test_module_import_does_not_leak_pythonpath_into_os_environ() -> None:
    # Regression: importing this module must not mutate the process-wide
    # ``os.environ["PYTHONPATH"]``. A module-level assignment leaks the src path
    # into every later subprocess in the whole pytest run and poisoned the
    # installed-wheel console-script gate (its checker then imported synapse_channel
    # from src instead of the built wheel). Import the module in a fresh interpreter
    # with a known PYTHONPATH and confirm the value is untouched.
    tests_dir = Path(__file__).resolve().parent
    src_dir = tests_dir.parent / "src"
    known = os.pathsep.join([str(tests_dir), str(src_dir)])
    child_env = {**os.environ, "PYTHONPATH": known}
    code = (
        "import os\n"
        "expected = os.environ['PYTHONPATH']\n"
        "import test_cli_e2e_grok_claim_hook\n"
        "actual = os.environ['PYTHONPATH']\n"
        "assert actual == expected, (expected, actual)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=child_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

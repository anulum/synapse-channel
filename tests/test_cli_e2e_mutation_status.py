# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real CLI/Git/provider mutation-posture journeys
"""Exercise mutation posture through real subprocess, files, and Git hooks."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import CliResult, _candidate_environment, git_repo, git_run, run_cli
from synapse_channel import cli
from synapse_channel.mutation_governance import _READ_LIMIT, inspect_mutation_governance


def _launchable_beside_python(name: str) -> Path:
    """Return the console script installed next to this interpreter.

    pip on Windows writes ``name.exe``. CreateProcess does not apply PATHEXT
    to an exact path, so the extensionless POSIX name is not that file.
    """
    directory = Path(sys.executable).parent
    if os.name == "nt":
        for suffix in (".exe", ".cmd", ".bat", ".com"):
            candidate = directory / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
    return directory / name


_SYNAPSE_BIN = _launchable_beside_python("synapse")
_PRE_COMMIT_BIN = Path(
    os.environ.get(
        "SYNAPSE_TEST_PRE_COMMIT_BIN",
        str(_launchable_beside_python("pre-commit")),
    )
)
# Windows stores no execute bit: a regular file stays 0o666 whatever chmod
# asked for, so a real pre-commit hook reads as not installed. The
# configuration read is the same on every platform.
_POSIX_EXECUTE_BIT = os.name != "nt"


def _run(*args: str, cwd: Path) -> CliResult:
    return run_cli(*args, cwd=cwd)


def _private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _break_owner_only(path: Path) -> str:
    """Make ``path`` fail its owner-only floor and return the detail that names it.

    POSIX stores that floor in the mode bits. Windows keeps a normal file at
    mode 0o666 and proves the floor with the DACL, so chmod cannot create it.
    """
    if os.name == "nt":
        completed = subprocess.run(
            ["icacls", os.fspath(path), "/grant", "*S-1-1-0:(R)"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return "not owner-only"
    path.chmod(0o666)
    return "writable by group or others"


def _record_hooks_path(repo: Path, value: str) -> None:
    """Record ``core.hooksPath`` so Git will print it back.

    CreateProcess rejects an argument of this length before Git starts
    (WinError 206). The inspection limit is about Git's answer, so on
    Windows the value is written into the repository config.
    """
    if os.name != "nt":
        git_run(repo, "config", "core.hooksPath", value)
        return
    config = repo / ".git" / "config"
    config.write_text(
        config.read_text(encoding="utf-8") + f"\n[core]\n\thooksPath = {value}\n",
        encoding="utf-8",
        newline="\n",
    )


def _provider_fragment(repo: Path, provider: str) -> str:
    rendered = _run(
        "adapters",
        f"{provider}-claim-hook",
        "--identity",
        f"repo/{provider}",
        "--synapse-bin",
        str(_SYNAPSE_BIN),
        "--print-config",
        cwd=repo,
    )
    assert rendered.ok(), rendered.output
    return rendered.stdout


def _run_in_process(*args: str) -> CliResult:
    """Drive the public CLI dispatcher while retaining parent-process coverage."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        returncode = cli.main(list(args))
    return CliResult(tuple(args), returncode, stdout.getvalue(), stderr.getvalue())


def _report(home: Path, repo: Path) -> dict[str, object]:
    args = (
        "adapters",
        "mutation-status",
        "--home",
        str(home),
        "--project",
        str(repo),
        "--json",
    )
    result = _run(*args, cwd=repo)
    assert result.ok(), result.output
    decoded = json.loads(result.stdout)
    assert isinstance(decoded, dict)
    in_process = _run_in_process(*args)
    assert in_process.ok(), in_process.output
    assert json.loads(in_process.stdout) == decoded
    direct = inspect_mutation_governance(
        home=home,
        project=repo,
        opencode_config_root=home / ".config",
    )
    assert json.loads(json.dumps(direct.to_dict())) == decoded
    return decoded


def _provider_states(report: dict[str, object]) -> dict[str, str]:
    providers = report["providers"]
    assert isinstance(providers, list)
    return {
        item["provider"]: item["configuration_state"]
        for item in providers
        if isinstance(item, dict)
    }


def test_real_cli_reports_absent_guards_without_writing(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)

    report = _report(home, repo)

    assert report["schema"] == "synapse.mutation-governance.v1"
    assert report["read_only"] is True
    assert report["overall_enforcement"] == "not-verified"
    assert _provider_states(report) == {
        "claude": "not-configured",
        "codex": "not-configured",
        "gemini": "not-configured",
        "grok": "not-configured",
        "kimi": "not-configured",
        "opencode": "not-configured",
    }
    assert list(home.iterdir()) == []
    git_run(repo, "diff", "--exit-code")

    text = _run(
        "adapters",
        "mutation-status",
        "--home",
        str(home),
        "--project",
        str(repo),
        cwd=repo,
    )
    assert text.ok(), text.output
    assert "Overall enforcement: not-verified" in text.stdout
    assert "unsupported custom write tools" in text.stdout
    assert "direct filesystem writes" in text.stdout
    assert "provider hook crash or timeout" in text.stdout
    assert "Staged Git claim gate" in text.stdout
    in_process_text = _run_in_process(
        "adapters",
        "mutation-status",
        "--home",
        str(home),
        "--project",
        str(repo),
    )
    assert in_process_text.ok(), in_process_text.output
    assert in_process_text.stdout == text.stdout
    providers = report["providers"]
    assert isinstance(providers, list)
    codex = next(
        item for item in providers if isinstance(item, dict) and item.get("provider") == "codex"
    )
    assert codex["covered_write_tools"] == ["apply_patch", "Bash"]
    assert codex["residuals"] == [
        "write_stdin does not repeat PreToolUse for an existing exec_command session",
        "MCP and future write-capable tools outside this recipe matcher",
    ]


def test_real_generated_provider_configs_and_precommit_gate_are_detected(
    tmp_path: Path,
) -> None:
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)

    destinations = {
        "claude": home / ".claude" / "settings.json",
        "codex": home / ".codex" / "hooks.json",
        "gemini": home / ".gemini" / "settings.json",
        "grok": home / ".grok" / "hooks" / "synapse.json",
    }
    for provider, destination in destinations.items():
        _private_write(destination, _provider_fragment(repo, provider))

    kimi_path = home / ".kimi-code" / "config.toml"
    kimi = _run(
        "adapters",
        "kimi-claim-hook",
        "--identity",
        "repo/kimi",
        "--synapse-bin",
        str(_SYNAPSE_BIN),
        "--kimi-config",
        str(kimi_path),
        "--install-config",
        cwd=repo,
    )
    assert kimi.ok(), kimi.output

    opencode = _run(
        "adapters",
        "opencode",
        "install",
        "--scope",
        "global",
        "--project",
        str(repo),
        "--home",
        str(home),
        "--config-root",
        str(home / ".config"),
        "--identity",
        "repo/opencode",
        "--synapse-bin",
        str(_SYNAPSE_BIN),
        cwd=repo,
    )
    assert opencode.ok(), opencode.output

    precommit_config = repo / ".pre-commit-config.yaml"
    precommit_config.write_text(
        """repos:
  - repo: local
    hooks:
      - id: staged-claim-coverage
        name: every staged path has an owned Synapse claim
        entry: synapse git-claim-check --staged
        language: system
        stages: [pre-commit]
        always_run: true
        pass_filenames: false
""",
        encoding="utf-8",
    )
    installed = subprocess.run(  # noqa: S603 - configured real tool and isolated repo
        [str(_PRE_COMMIT_BIN), "install", "--config", str(precommit_config)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr

    guarded_files = (
        *destinations.values(),
        kimi_path,
        home / ".config" / "opencode" / "opencode.json",
    )
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        for path in guarded_files
    }
    report = _report(home, repo)

    assert _provider_states(report) == dict.fromkeys(
        set(destinations) | {"kimi", "opencode"}, "configured"
    )
    providers = report["providers"]
    assert isinstance(providers, list)
    assert all(
        item["enforcement_status"] == "not-verified" for item in providers if isinstance(item, dict)
    )
    gate = report["staged_gate"]
    assert isinstance(gate, dict)
    assert gate["configuration_state"] == "configured"
    if _POSIX_EXECUTE_BIT:
        assert gate["hook_state"] == "installed"
        assert gate["gate_status"] == "ready-not-exercised"
        assert gate["enforcement_status"] == "not-exercised"
    else:
        assert gate["hook_state"] == "invalid"
        assert gate["gate_status"] == "incomplete"
        assert gate["enforcement_status"] == "not-configured"
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        for path in guarded_files
    }


def test_real_cli_reports_invalid_and_partial_configuration(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    _private_write(home / ".codex" / "hooks.json", "{not-json\n")
    unsafe_gemini = home / ".gemini" / "settings.json"
    _private_write(unsafe_gemini, _provider_fragment(repo, "gemini"))
    unsafe_detail = _break_owner_only(unsafe_gemini)

    installed = _run(
        "adapters",
        "opencode",
        "install",
        "--scope",
        "project",
        "--project",
        str(repo),
        "--home",
        str(home),
        "--identity",
        "repo/opencode",
        "--synapse-bin",
        str(_SYNAPSE_BIN),
        cwd=repo,
    )
    assert installed.ok(), installed.output
    (repo / ".opencode" / "opencode.json").unlink()

    report = _report(home, repo)
    states = _provider_states(report)
    assert states["codex"] == "invalid"
    assert states["gemini"] == "invalid"
    assert states["opencode"] == "partial"
    providers = report["providers"]
    assert isinstance(providers, list)
    gemini = next(
        item for item in providers if isinstance(item, dict) and item.get("provider") == "gemini"
    )
    assert unsafe_detail in gemini["configuration_detail"]


def test_real_cli_treats_malformed_hook_commands_as_not_configured(tmp_path: Path) -> None:
    """Valid provider JSON with non-command values never becomes configured."""
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    settings = home / ".claude" / "settings.json"
    _private_write(
        settings,
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"command": "'unterminated"},
                        {"command": 7},
                        {"command": True},
                        {"command": None},
                    ]
                }
            }
        ),
    )
    before = (settings.read_bytes(), settings.stat().st_mtime_ns, settings.stat().st_mode)

    report = _report(home, repo)

    assert _provider_states(report)["claude"] == "not-configured"
    assert before == (settings.read_bytes(), settings.stat().st_mtime_ns, settings.stat().st_mode)


def test_real_cli_reports_unsafe_grok_hook_directory(tmp_path: Path) -> None:
    """A symlinked provider hook directory fails closed without traversal."""
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    outside = tmp_path / "outside-hooks"
    outside.mkdir(mode=0o700)
    fragment = _provider_fragment(repo, "grok")
    _private_write(outside / "synapse.json", fragment)
    grok_root = home / ".grok"
    grok_root.mkdir(mode=0o700)
    (grok_root / "hooks").symlink_to(outside, target_is_directory=True)

    report = _report(home, repo)

    providers = report["providers"]
    assert isinstance(providers, list)
    grok = next(
        item for item in providers if isinstance(item, dict) and item.get("provider") == "grok"
    )
    assert grok["configuration_state"] == "invalid"
    assert "cannot safely enumerate" in grok["configuration_detail"]
    assert (outside / "synapse.json").read_text(encoding="utf-8") == fragment


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes are required")
def test_real_cli_reports_unreadable_grok_hook_boundaries(tmp_path: Path) -> None:
    """Real POSIX permission denial covers both parent and directory reads."""
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    grok_root = home / ".grok"
    hooks = grok_root / "hooks"
    hooks.mkdir(parents=True, mode=0o700)
    config = hooks / "synapse.json"
    _private_write(config, _provider_fragment(repo, "grok"))
    before = config.read_bytes()

    grok_root.chmod(0)
    try:
        parent_denied = _report(home, repo)
    finally:
        grok_root.chmod(0o700)
    assert _provider_states(parent_denied)["grok"] == "invalid"

    hooks.chmod(0)
    try:
        directory_denied = _report(home, repo)
    finally:
        hooks.chmod(0o700)
    assert _provider_states(directory_denied)["grok"] == "invalid"
    assert config.read_bytes() == before


def test_real_cli_distinguishes_unowned_and_invalid_kimi_config(tmp_path: Path) -> None:
    """A real Kimi file is either unowned or invalid, never silently configured."""
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    config = home / ".kimi-code" / "config.toml"
    _private_write(config, 'model = "kimi-k2"\n')

    unowned = _report(home, repo)
    assert _provider_states(unowned)["kimi"] == "not-configured"

    _private_write(config, "[[hooks]\n")
    before = (config.read_bytes(), config.stat().st_mtime_ns, config.stat().st_mode)
    invalid = _report(home, repo)
    assert _provider_states(invalid)["kimi"] == "invalid"
    assert before == (config.read_bytes(), config.stat().st_mtime_ns, config.stat().st_mode)


def test_real_cli_reports_invalid_opencode_schema_outside_git(tmp_path: Path) -> None:
    """Invalid OpenCode structure and a non-repository staged gate both fail closed."""
    project = tmp_path / "not-a-repository"
    project.mkdir(mode=0o700)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    config = home / ".config" / "opencode" / "opencode.json"
    _private_write(config, json.dumps({"mcp": []}))
    before = (config.read_bytes(), config.stat().st_mtime_ns, config.stat().st_mode)

    report = _report(home, project)

    assert _provider_states(report)["opencode"] == "invalid"
    gate = report["staged_gate"]
    assert isinstance(gate, dict)
    assert gate["repository"] is None
    assert gate["configuration_state"] == "not-configured"
    assert gate["hook_state"] == "not-a-repository"
    assert gate["gate_status"] == "not-configured"
    assert before == (config.read_bytes(), config.stat().st_mtime_ns, config.stat().st_mode)
    text = _run_in_process(
        "adapters", "mutation-status", "--home", str(home), "--project", str(project)
    )
    assert text.ok(), text.output
    assert "  repository: not-a-repository" in text.stdout
    assert "  hook: not-a-repository" in text.stdout


def test_real_cli_fails_closed_when_git_answers_beyond_the_inspection_limit(
    tmp_path: Path,
) -> None:
    """A Git answer over the 64 KiB limit is refused and named, not read as a non-repository.

    A real ``core.hooksPath`` of 70 000 characters makes ``git rev-parse
    --git-path hooks/pre-commit`` print more than the limit; the inspection
    must not read on and must not call the repository gated.
    """
    repo = git_repo(tmp_path / "repo")
    _record_hooks_path(repo, "/" + "h" * 70_000)
    answer = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks/pre-commit"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    assert len(answer.encode("utf-8")) > 65_536
    home = tmp_path / "home"
    home.mkdir(mode=0o700)

    report = _report(home, repo)

    gate = report["staged_gate"]
    assert isinstance(gate, dict)
    assert gate["repository"] is None
    assert gate["hook_state"] == "inspection-limit"
    assert gate["gate_status"] == "not-configured"
    assert list(home.iterdir()) == []
    text = _run(
        "adapters", "mutation-status", "--home", str(home), "--project", str(repo), cwd=repo
    )
    assert text.ok(), text.output
    assert "repository: not inspected (Git answer over the inspection limit)" in text.stdout
    assert "hook: inspection-limit" in text.stdout
    in_process_text = _run_in_process(
        "adapters", "mutation-status", "--home", str(home), "--project", str(repo)
    )
    assert in_process_text.stdout == text.stdout


def test_real_cli_reports_unsafe_staged_gate_files(tmp_path: Path) -> None:
    """Symlinked pre-commit policy and hook files produce an incomplete gate."""
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    outside_config = tmp_path / "outside-pre-commit.yaml"
    outside_config.write_text("repos: []\n", encoding="utf-8")
    outside_hook = tmp_path / "outside-pre-commit-hook"
    outside_hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    outside_hook.chmod(0o700)
    (repo / ".pre-commit-config.yaml").symlink_to(outside_config)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.symlink_to(outside_hook)
    before = (outside_config.read_bytes(), outside_hook.read_bytes())

    report = _report(home, repo)

    gate = report["staged_gate"]
    assert isinstance(gate, dict)
    assert gate["configuration_state"] == "invalid"
    assert gate["hook_state"] == "invalid"
    assert gate["gate_status"] == "incomplete"
    assert (outside_config.read_bytes(), outside_hook.read_bytes()) == before


_GATE_THAT_WOULD_LOOK_CONFIGURED = """\
repos:
- repo: local
  hooks:
  - id: staged-claim-coverage
    stages: [pre-commit]
    always_run: true
    pass_filenames: false
    entry: run_staged_claim_hook.py
"""

_OPEN_SWAP_PROGRAM = r"""
import contextlib
import io
import json
import os
import sys
from pathlib import Path

config, replacement, mode, home, repo, limit_text = sys.argv[1:7]
limit = int(limit_text)
armed = True

def swap_at_open(event, args):
    global armed
    if not armed or event != "open" or not args or args[0] != config:
        return
    armed = False
    if mode == "replaced-inode":
        Path(replacement).replace(config)
        return
    extra = (limit + 1) - Path(config).stat().st_size
    descriptor = os.open(config, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(descriptor, b"x" * extra)
    finally:
        os.close(descriptor)

sys.addaudithook(swap_at_open)
if os.environ.get("READ_DIRECT") == "1":
    from synapse_channel.mutation_governance import _read_repository_text

    try:
        _read_repository_text(Path(config))
    except ValueError as exc:
        sys.stdout.write(str(exc))
        raise SystemExit(0)
    raise SystemExit("the read succeeded")
from synapse_channel.cli import main

captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    code = main([
        "adapters", "mutation-status", "--home", home, "--project", repo, "--json",
    ])
if code != 0 or armed:
    raise SystemExit(f"code={code} armed={armed}\n{captured.getvalue()}")
json.loads(captured.getvalue())
sys.stdout.write(captured.getvalue())
"""


@pytest.mark.parametrize("mode", ["replaced-inode", "grown-past-limit"])
def test_real_cli_refuses_a_precommit_file_that_changes_before_open(
    tmp_path: Path, mode: str
) -> None:
    """A real rename or append at open must not become a configured gate."""
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    config = repo / ".pre-commit-config.yaml"
    # newline="\n" keeps the LF the later byte assertion names. The default
    # writer would store the platform line ending, which on Windows is CRLF.
    config.write_text("repos: []\n", encoding="utf-8", newline="\n")
    before = config.stat()
    replacement = tmp_path / "replacement.yaml"
    replacement.write_text(_GATE_THAT_WOULD_LOOK_CONFIGURED, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _OPEN_SWAP_PROGRAM,
            str(config),
            str(replacement),
            mode,
            str(home),
            str(repo),
            str(_READ_LIMIT),
        ],
        cwd=repo,
        env=_candidate_environment(None),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    gate = report["staged_gate"]
    assert isinstance(gate, dict)
    assert gate["repository"] is not None
    assert gate["configuration_state"] == "invalid"
    assert gate["hook_state"] == "not-installed"
    assert gate["gate_status"] == "incomplete"
    after = config.stat()
    if mode == "replaced-inode":
        assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        assert config.read_text(encoding="utf-8") == _GATE_THAT_WOULD_LOOK_CONFIGURED
    else:
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        assert after.st_size > _READ_LIMIT
        assert config.read_bytes().startswith(b"repos: []\n")


def test_an_append_at_open_is_refused_before_the_read_loop(tmp_path: Path) -> None:
    """Growing past the limit at open is the stat refusal, not the read-loop limit."""
    repo = git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    config = repo / ".pre-commit-config.yaml"
    config.write_text("repos: []\n", encoding="utf-8")
    replacement = tmp_path / "replacement.yaml"
    replacement.write_text(_GATE_THAT_WOULD_LOOK_CONFIGURED, encoding="utf-8")
    env = _candidate_environment({"READ_DIRECT": "1"})
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _OPEN_SWAP_PROGRAM,
            str(config),
            str(replacement),
            "grown-past-limit",
            str(home),
            str(repo),
            str(_READ_LIMIT),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "repository inspection path changed" in result.stdout
    assert "exceeds" not in result.stdout

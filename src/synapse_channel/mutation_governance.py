# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only provider and staged-index mutation posture
"""Inspect configured mutation guards without overstating enforcement.

Configuration files can prove that an owned Synapse recipe is present. They
cannot prove that a provider loaded it, invoked it for every write path, or
failed closed. This module therefore reports runtime detection, configuration,
and enforcement as separate facts. Its public report is deliberately read-only
and never labels a statically inspected provider hook as enforced.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess  # nosec B404
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from synapse_channel.kimi_hook_config_file import (
    KimiHookConfigFileError,
    read_config_snapshot,
    resolve_kimi_config_path,
)
from synapse_channel.kimi_hook_installer import (
    KimiHookInstallerError,
    contains_hook_block,
    validate_config_toml,
)
from synapse_channel.opencode_adapter import (
    OpenCodeAdapterError,
    parse_config,
    plugin_is_owned,
    resolve_opencode_paths,
)
from synapse_channel.opencode_adapter_files import (
    OpenCodeAdapterFileError,
    read_text_snapshot,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib

ConfigurationState = Literal["configured", "partial", "not-configured", "invalid"]
HookState = Literal["installed", "not-installed", "invalid", "not-a-repository"]

GLOBAL_RESIDUALS = (
    "unsupported custom write tools",
    "MCP write tools",
    "direct filesystem writes",
    "provider hook crash or timeout",
    "incomplete provider interception",
    "external side effects such as network or service mutation",
)
_READ_LIMIT = 1_048_576


@dataclass(frozen=True)
class ProviderPosture:
    """One provider's detected runtime, owned configuration, and honest boundary."""

    provider: str
    runtime_detected: bool
    configuration_state: ConfigurationState
    configuration_detail: str
    enforcement_status: Literal["not-verified"]
    inspected_paths: tuple[str, ...]
    covered_write_tools: tuple[str, ...]
    residuals: tuple[str, ...]


@dataclass(frozen=True)
class StagedGatePosture:
    """Static readiness of the independent staged-index claim gate."""

    repository: str | None
    configuration_state: ConfigurationState
    hook_state: HookState
    gate_status: Literal["ready-not-exercised", "incomplete", "not-configured"]
    enforcement_status: Literal["not-exercised", "not-configured"]
    inspected_paths: tuple[str, ...]
    covered_surface: tuple[str, ...]
    residuals: tuple[str, ...]


@dataclass(frozen=True)
class MutationGovernanceReport:
    """Stable JSON-ready mutation-governance posture."""

    schema: Literal["synapse.mutation-governance.v1"]
    read_only: Literal[True]
    overall_enforcement: Literal["not-verified"]
    providers: tuple[ProviderPosture, ...]
    staged_gate: StagedGatePosture
    unmediated_residuals: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable mapping with stable field names."""
        return asdict(self)


@dataclass(frozen=True)
class _ProviderSpec:
    name: str
    executable: str
    hook_command: str
    paths: tuple[Path, ...]
    covered_tools: tuple[str, ...]
    residuals: tuple[str, ...]


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _contains_command(value: object, hook_command: str) -> bool:
    """Recognise an exact ``adapters <hook>`` argv pair in nested JSON data."""
    if isinstance(value, str):
        try:
            tokens = shlex.split(value)
        except ValueError:
            return False
        return any(
            tokens[index] == "adapters" and tokens[index + 1] == hook_command
            for index in range(len(tokens) - 1)
        )
    if isinstance(value, list):
        if any(
            value[index] == "adapters" and value[index + 1] == hook_command
            for index in range(len(value) - 1)
        ):
            return True
        return any(_contains_command(item, hook_command) for item in value)
    if isinstance(value, dict):
        return any(_contains_command(item, hook_command) for item in value.values())
    return False


def _path_exists(path: Path) -> bool:
    """Check a candidate without following its final component."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _read_repository_text(path: Path) -> tuple[str, int] | None:
    """Read one bounded regular repository file without following a leaf symlink."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_size > _READ_LIMIT:
        raise ValueError(f"unsafe repository inspection path: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_size > _READ_LIMIT
        ):
            raise ValueError(f"repository inspection path changed: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, _READ_LIMIT + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _READ_LIMIT:
                raise ValueError(f"repository inspection path exceeds {_READ_LIMIT} bytes: {path}")
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    return data.decode("utf-8", errors="strict"), stat.S_IMODE(after.st_mode)


def _inspect_json_paths(
    paths: tuple[Path, ...], hook_command: str
) -> tuple[ConfigurationState, str]:
    configured = False
    for path in paths:
        try:
            if not _path_exists(path):
                continue
            snapshot = read_text_snapshot(path)
            decoded = json.loads(snapshot.text)
        except (json.JSONDecodeError, OSError, OpenCodeAdapterFileError, UnicodeError) as exc:
            detail = str(exc).replace("OpenCode adapter", "provider configuration")
            return "invalid", f"cannot safely inspect {path}: {detail}"
        if _contains_command(decoded, hook_command):
            configured = True
    if configured:
        return "configured", "exact Synapse hook command found"
    return "not-configured", "no exact Synapse hook command found"


def _safe_json_files(directory: Path) -> tuple[Path, ...] | None:
    """Return direct JSON children, rejecting a missing/unsafe hook directory."""
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return ()
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return None
    try:
        return tuple(
            sorted(
                (entry for entry in directory.iterdir() if entry.name.endswith(".json")),
                key=lambda path: path.name,
            )
        )
    except OSError:
        return None


def _inspect_grok(home: Path) -> tuple[ConfigurationState, str, tuple[Path, ...]]:
    hooks_dir = home / ".grok" / "hooks"
    paths = _safe_json_files(hooks_dir)
    if paths is None:
        return "invalid", f"cannot safely enumerate {hooks_dir}", (hooks_dir,)
    state, detail = _inspect_json_paths(paths, "grok-claim-hook")
    return state, detail, paths or (hooks_dir,)


def _inspect_kimi(home: Path) -> tuple[ConfigurationState, str, Path]:
    path = resolve_kimi_config_path(None, environ={}, home=home)
    try:
        snapshot = read_config_snapshot(path)
        if not snapshot.existed:
            return "not-configured", "Kimi config does not exist", path
        validate_config_toml(snapshot.text)
        decoded = tomllib.loads(snapshot.text)
        configured = contains_hook_block(snapshot.text) and _contains_command(
            decoded, "kimi-claim-hook"
        )
    except (KimiHookConfigFileError, KimiHookInstallerError, OSError, UnicodeError) as exc:
        return "invalid", f"cannot safely inspect {path}: {exc}", path
    if configured:
        return "configured", "owned marker and exact Synapse hook command found", path
    return "not-configured", "owned Kimi hook block not found", path


def _inspect_opencode_scope(
    *, scope: str, home: Path, project: Path, config_root: Path | None
) -> tuple[ConfigurationState, str, tuple[Path, ...]]:
    paths = resolve_opencode_paths(
        scope=scope,
        project=project,
        home=home,
        config_root=config_root,
    )
    inspected = (paths.config, paths.plugin)
    try:
        config_snapshot = read_text_snapshot(paths.config) if _path_exists(paths.config) else None
        plugin_snapshot = read_text_snapshot(paths.plugin) if _path_exists(paths.plugin) else None
        config = parse_config(config_snapshot.text if config_snapshot else "")
        mcp = config.get("mcp")
        if mcp is not None and not isinstance(mcp, dict):
            raise OpenCodeAdapterError("OpenCode config field 'mcp' must be an object.")
        entry = mcp.get("synapse") if isinstance(mcp, dict) else None
        config_owned = (
            isinstance(entry, dict)
            and isinstance(entry.get("environment"), dict)
            and entry["environment"].get("SYNAPSE_ADAPTER_OWNER") == "synapse-channel"
        )
        plugin_owned = (
            plugin_snapshot is not None
            and plugin_is_owned(plugin_snapshot.text)
            and "opencode-claim-hook" in plugin_snapshot.text
        )
    except (OSError, ValueError, OpenCodeAdapterError, OpenCodeAdapterFileError) as exc:
        return "invalid", f"cannot safely inspect OpenCode assets: {exc}", inspected
    if config_owned and plugin_owned:
        return "configured", "owned MCP entry and claim-hook plugin found", inspected
    if config_owned or plugin_owned:
        return (
            "partial",
            "only one of the owned MCP entry and claim-hook plugin was found",
            inspected,
        )
    return "not-configured", "owned OpenCode assets not found", inspected


def _inspect_opencode(
    *, home: Path, project: Path, config_root: Path | None
) -> tuple[ConfigurationState, str, tuple[Path, ...]]:
    project_result = _inspect_opencode_scope(
        scope="project", home=home, project=project, config_root=config_root
    )
    global_result = _inspect_opencode_scope(
        scope="global", home=home, project=project, config_root=config_root
    )
    results = (project_result, global_result)
    paths = tuple(path for _, _, inspected in results for path in inspected)
    invalid = [detail for state, detail, _ in results if state == "invalid"]
    if invalid:
        return "invalid", "; ".join(invalid), paths
    partial = [detail for state, detail, _ in results if state == "partial"]
    if partial:
        return "partial", "; ".join(partial), paths
    configured = [detail for state, detail, _ in results if state == "configured"]
    if configured:
        return "configured", "; ".join(configured), paths
    return "not-configured", "owned project/global OpenCode assets not found", paths


def _run_git(project: Path, *arguments: str) -> str:
    command = ["git", "-C", str(project), *arguments]
    # The command is a fixed Git executable with structured arguments and no shell.
    completed = subprocess.run(  # nosec B603
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    if completed.returncode != 0:
        raise ValueError("not a readable Git repository")
    if len(completed.stdout.encode("utf-8")) > 65_536:
        raise ValueError("Git response exceeded the inspection limit")
    return completed.stdout.strip()


def _contains_staged_gate_config(text: str) -> bool:
    required = (
        "id: staged-claim-coverage",
        "stages: [pre-commit]",
        "always_run: true",
        "pass_filenames: false",
    )
    command_present = "run_staged_claim_hook.py" in text or "git-claim-check --staged" in text
    return command_present and all(marker in text for marker in required)


def _inspect_staged_gate(project: Path) -> StagedGatePosture:
    residuals = (
        "unstaged working-tree changes",
        "direct filesystem writes before staging",
        "external side effects outside the Git index",
    )
    try:
        root = Path(_run_git(project, "rev-parse", "--show-toplevel"))
        hook_raw = Path(_run_git(project, "rev-parse", "--git-path", "hooks/pre-commit"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return StagedGatePosture(
            repository=None,
            configuration_state="not-configured",
            hook_state="not-a-repository",
            gate_status="not-configured",
            enforcement_status="not-configured",
            inspected_paths=(),
            covered_surface=("staged Git index",),
            residuals=residuals,
        )

    root = _lexical_absolute(root)
    hook_path = hook_raw if hook_raw.is_absolute() else root / hook_raw
    config_path = root / ".pre-commit-config.yaml"
    configuration: ConfigurationState = "not-configured"
    hook_state: HookState = "not-installed"
    direct_gate = False
    try:
        config_snapshot = _read_repository_text(config_path)
        if config_snapshot is not None and _contains_staged_gate_config(config_snapshot[0]):
            configuration = "configured"
    except (OSError, UnicodeError, ValueError):
        configuration = "invalid"
    try:
        hook_snapshot = _read_repository_text(hook_path)
        if hook_snapshot is not None:
            hook_text, hook_mode = hook_snapshot
            executable = bool(hook_mode & 0o111)
            pre_commit_runner = "pre_commit" in hook_text
            direct_gate = "git-claim-check --staged" in hook_text
            hook_state = (
                "installed" if executable and (pre_commit_runner or direct_gate) else "invalid"
            )
    except (OSError, UnicodeError, ValueError):
        hook_state = "invalid"

    ready = hook_state == "installed" and (configuration == "configured" or direct_gate)
    incomplete = configuration != "not-configured" or hook_state != "not-installed"
    return StagedGatePosture(
        repository=str(root),
        configuration_state=configuration,
        hook_state=hook_state,
        gate_status="ready-not-exercised"
        if ready
        else ("incomplete" if incomplete else "not-configured"),
        enforcement_status="not-exercised" if ready else "not-configured",
        inspected_paths=(str(config_path), str(hook_path)),
        covered_surface=("staged Git index",),
        residuals=residuals,
    )


def inspect_mutation_governance(
    *, home: Path, project: Path, opencode_config_root: Path | None = None
) -> MutationGovernanceReport:
    """Inspect real provider files and Git metadata without changing either."""
    home = _lexical_absolute(home)
    project = _lexical_absolute(project)
    common_specs = (
        _ProviderSpec(
            "claude",
            "claude",
            "claude-claim-hook",
            (home / ".claude" / "settings.json", project / ".claude" / "settings.json"),
            ("Edit", "Write", "Bash"),
            ("MCP, custom, and future write-capable tools", "host-dependent crash or timeout"),
        ),
        _ProviderSpec(
            "codex",
            "codex",
            "codex-claim-hook",
            (home / ".codex" / "hooks.json", project / ".codex" / "hooks.json"),
            ("apply_patch", "Bash"),
            ("incomplete unified_exec interception", "MCP and future write-capable tools"),
        ),
        _ProviderSpec(
            "gemini",
            "gemini",
            "gemini-claim-hook",
            (home / ".gemini" / "settings.json", project / ".gemini" / "settings.json"),
            ("replace", "write_file", "run_shell_command"),
            (
                "MCP, custom, and future write-capable tools",
                "non-JSON crash or timeout may fail open",
            ),
        ),
    )
    providers: list[ProviderPosture] = []
    for spec in common_specs:
        state, detail = _inspect_json_paths(spec.paths, spec.hook_command)
        providers.append(
            ProviderPosture(
                provider=spec.name,
                runtime_detected=shutil.which(spec.executable) is not None,
                configuration_state=state,
                configuration_detail=detail,
                enforcement_status="not-verified",
                inspected_paths=tuple(str(path) for path in spec.paths),
                covered_write_tools=spec.covered_tools,
                residuals=spec.residuals,
            )
        )

    grok_state, grok_detail, grok_paths = _inspect_grok(home)
    providers.append(
        ProviderPosture(
            provider="grok",
            runtime_detected=shutil.which("grok") is not None,
            configuration_state=grok_state,
            configuration_detail=grok_detail,
            enforcement_status="not-verified",
            inspected_paths=tuple(str(path) for path in grok_paths),
            covered_write_tools=(
                "search_replace",
                "write",
                "Edit",
                "Write",
                "MultiEdit",
                "run_terminal_command",
            ),
            residuals=("custom and future write-capable tools", "host crash or timeout fails open"),
        )
    )

    kimi_state, kimi_detail, kimi_path = _inspect_kimi(home)
    providers.append(
        ProviderPosture(
            provider="kimi",
            runtime_detected=shutil.which("kimi") is not None,
            configuration_state=kimi_state,
            configuration_detail=kimi_detail,
            enforcement_status="not-verified",
            inspected_paths=(str(kimi_path),),
            covered_write_tools=("Edit", "Write", "Bash"),
            residuals=("custom and future write-capable tools", "host crash or timeout fails open"),
        )
    )

    opencode_state, opencode_detail, opencode_paths = _inspect_opencode(
        home=home, project=project, config_root=opencode_config_root
    )
    providers.append(
        ProviderPosture(
            provider="opencode",
            runtime_detected=shutil.which("opencode") is not None,
            configuration_state=opencode_state,
            configuration_detail=opencode_detail,
            enforcement_status="not-verified",
            inspected_paths=tuple(str(path) for path in opencode_paths),
            covered_write_tools=("edit", "write", "apply_patch", "bash"),
            residuals=("custom, MCP, and future write-capable tools",),
        )
    )

    return MutationGovernanceReport(
        schema="synapse.mutation-governance.v1",
        read_only=True,
        overall_enforcement="not-verified",
        providers=tuple(providers),
        staged_gate=_inspect_staged_gate(project),
        unmediated_residuals=GLOBAL_RESIDUALS,
    )

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-agent adapter catalogue and pure install/uninstall planning
"""Cross-agent adapter kits: route an existing coding tool to the hub.

An adapter is the thin, claim-aware glue that wires a specific coding tool — Claude
Code, Codex, Cursor, Aider, Copilot, Windsurf, Gemini CLI — into Synapse so the tool
claims its file scope before editing and releases it on commit, without Synapse
pretending to be that tool or shipping a persona. Every adapter carries the *same*
small contract (claim before edit, release on commit via the git hooks, reach the
hub) rendered into the tool's own conventions format, between explicit markers so it
can be removed exactly.

This module is the **pure** half: the tool catalogue, detection logic, target
resolution, contract rendering, and the string transforms that plan an install or an
uninstall. It performs no filesystem I/O — :mod:`synapse_channel.cli_adapters` reads
and writes files using these planners, so the policy here is fully testable without
touching a real home directory. The adapter adds no new coordination primitive; it
only points a tool at the claims, releases, and presence that already exist.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MARKER_BEGIN = "synapse-channel:adapter:begin"
"""Sentinel opening the Synapse-owned block, so a write can be found and removed exactly."""

MARKER_END = "synapse-channel:adapter:end"
"""Sentinel closing the Synapse-owned block."""

FILE_MODE = "file"
"""Dedicated-file adapter: the whole file is Synapse's, so uninstall deletes it."""

APPEND_MODE = "append"
"""Shared-file adapter: a marked block is added to a file the tool also owns."""

HOME_SCOPE = "home"
"""The adapter target is resolved under the user's home directory."""

PROJECT_SCOPE = "project"
"""The adapter target is resolved under the project working directory."""


@dataclass(frozen=True)
class AdapterTool:
    """One coding tool the kit can wire to the hub.

    Attributes
    ----------
    key : str
        Stable lowercase identifier used on the CLI (``claude-code``, ``cursor``).
    label : str
        Human-readable name.
    binaries : tuple[str, ...]
        Executable names that, if found on ``PATH``, mark the tool installed.
    detect_paths : tuple[str, ...]
        Home-relative paths whose existence also marks the tool installed.
    target : str
        Path of the adapter file, relative to its scope.
    scope : str
        :data:`HOME_SCOPE` or :data:`PROJECT_SCOPE` — what ``target`` is relative to.
    mode : str
        :data:`FILE_MODE` (Synapse owns the whole file) or :data:`APPEND_MODE`
        (a marked block inside a file the tool also uses).
    comment : str
        Marker comment style: ``"html"`` (``<!-- … -->``) or ``"hash"`` (``# …``).
    """

    key: str
    label: str
    binaries: tuple[str, ...]
    detect_paths: tuple[str, ...]
    target: str
    scope: str
    mode: str
    comment: str


CATALOGUE: tuple[AdapterTool, ...] = (
    AdapterTool(
        key="claude-code",
        label="Claude Code",
        binaries=("claude",),
        detect_paths=(".claude",),
        target=".claude/synapse.md",
        scope=HOME_SCOPE,
        mode=FILE_MODE,
        comment="html",
    ),
    AdapterTool(
        key="codex",
        label="Codex",
        binaries=("codex",),
        detect_paths=(".codex",),
        target=".codex/synapse.md",
        scope=HOME_SCOPE,
        mode=FILE_MODE,
        comment="html",
    ),
    AdapterTool(
        key="cursor",
        label="Cursor",
        binaries=("cursor",),
        detect_paths=(".cursor",),
        target=".cursor/rules/synapse.mdc",
        scope=PROJECT_SCOPE,
        mode=FILE_MODE,
        comment="html",
    ),
    AdapterTool(
        key="aider",
        label="Aider",
        binaries=("aider",),
        detect_paths=(),
        target="CONVENTIONS.md",
        scope=PROJECT_SCOPE,
        mode=APPEND_MODE,
        comment="html",
    ),
    AdapterTool(
        key="copilot",
        label="GitHub Copilot",
        binaries=("copilot",),
        detect_paths=(".github",),
        target=".github/synapse.md",
        scope=HOME_SCOPE,
        mode=FILE_MODE,
        comment="html",
    ),
    AdapterTool(
        key="windsurf",
        label="Windsurf",
        binaries=("windsurf",),
        detect_paths=(),
        target=".windsurfrules",
        scope=PROJECT_SCOPE,
        mode=APPEND_MODE,
        comment="hash",
    ),
    AdapterTool(
        key="gemini-cli",
        label="Gemini CLI",
        binaries=("gemini",),
        detect_paths=(".gemini",),
        target=".gemini/synapse.md",
        scope=HOME_SCOPE,
        mode=FILE_MODE,
        comment="html",
    ),
)
"""The tools the adapter kit can wire, surveyed against per-tool config conventions."""

_BY_KEY = {tool.key: tool for tool in CATALOGUE}


def tool_for(key: str) -> AdapterTool:
    """Return the catalogue tool for ``key``, raising :class:`KeyError` if unknown."""
    return _BY_KEY[key.strip().lower()]


def detect_installed(tool: AdapterTool, *, home: Path, which: Callable[[str], str | None]) -> bool:
    """Return whether ``tool`` looks installed: a binary on ``PATH`` or a config dir present."""
    if any(which(binary) for binary in tool.binaries):
        return True
    return any((home / path).exists() for path in tool.detect_paths)


def resolve_target(tool: AdapterTool, *, home: Path, project: Path) -> Path:
    """Return the absolute adapter-file path for ``tool`` under its scope."""
    root = home if tool.scope == HOME_SCOPE else project
    return root / tool.target


def _contract_body(*, identity: str, hub_uri: str) -> str:
    """Render the format-agnostic claim-safety contract carried by every adapter."""
    return (
        "## Synapse coordination (claim-safety)\n\n"
        "This workspace uses Synapse so parallel agents never edit the same files.\n\n"
        "- **Claim before edit:** run `synapse git-claim <task-id> --paths <path>` (or a\n"
        "  semantic claim) before modifying files, and treat a denied claim as a stop.\n"
        "- **Release on commit:** the git hooks installed by `synapse git-init` auto-release\n"
        "  the branch-scoped claims when you commit.\n"
        f"- **Reach the hub:** identity `{identity}`, hub `{hub_uri}`.\n"
        "- Coordination safety only — this carries no persona, workflow, or model behaviour.\n"
    )


def render_block(tool: AdapterTool, *, identity: str, hub_uri: str) -> str:
    """Render the marker-wrapped adapter block for ``tool`` in its comment style."""
    body = _contract_body(identity=identity, hub_uri=hub_uri)
    if tool.comment == "html":
        return f"<!-- {MARKER_BEGIN} -->\n{body}<!-- {MARKER_END} -->\n"
    return f"# {MARKER_BEGIN}\n{body}# {MARKER_END}\n"


def contains_block(text: str) -> bool:
    """Return whether ``text`` already carries a Synapse adapter block."""
    return MARKER_BEGIN in text and MARKER_END in text


def strip_block(text: str) -> str:
    """Return ``text`` with any Synapse adapter block (and its padding) removed."""
    lines = text.splitlines()
    begin = next((i for i, line in enumerate(lines) if MARKER_BEGIN in line), None)
    end = next((i for i, line in enumerate(lines) if MARKER_END in line), None)
    if begin is None or end is None or end < begin:
        return text
    remaining = lines[:begin] + lines[end + 1 :]
    while remaining and not remaining[0].strip():
        remaining.pop(0)
    while remaining and not remaining[-1].strip():
        remaining.pop()
    return "\n".join(remaining) + "\n" if remaining else ""


def plan_install(existing: str | None, block: str, *, mode: str) -> str:
    """Return the file content that installs ``block``, idempotently.

    In :data:`FILE_MODE` the adapter owns the whole file, so the planned content is
    just the block. In :data:`APPEND_MODE` any prior block is stripped first and the
    fresh block is appended after the file's own content, so re-installing replaces
    rather than duplicates.
    """
    if mode == FILE_MODE:
        return block
    base = strip_block(existing or "").rstrip()
    if not base:
        return block
    return f"{base}\n\n{block}"


def plan_uninstall(existing: str, *, mode: str) -> str | None:
    """Return the file content after removing the adapter, or ``None`` to delete the file.

    A :data:`FILE_MODE` adapter owns its file, so uninstall deletes it (``None``). An
    :data:`APPEND_MODE` adapter only strips its own marked block, leaving the rest of
    the tool's file intact.
    """
    if mode == FILE_MODE:
        return None
    return strip_block(existing)

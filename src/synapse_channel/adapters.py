# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-agent adapter catalogue and pure install/uninstall planning
"""Cross-agent adapter kits: route an existing coding tool to the hub.

An adapter renders claim conventions for a named coding tool — Claude Code, Codex,
Kimi Code, Cursor, Aider, Copilot, Windsurf, or Gemini CLI. Writing that file does
not prove that a host consumes it, enforce edits, or start a receiver. The contract
describes claiming before edits, configured Git release triggers, and the declared
identity and hub. Markers delimit its block; dedicated-file mode replaces or
deletes the whole target, while append mode preserves content outside the block.

Detection inspects installation footprints; rendering and install/uninstall
planning transform strings. :mod:`synapse_channel.cli_adapters` owns target-file
reads and writes. These planners can be tested with isolated inputs without
changing a real home directory. The adapter adds no new coordination primitive;
it points a tool at the claims, releases, and presence that already exist.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

MARKER_BEGIN = "synapse-channel:adapter:begin"
"""Sentinel opening the Synapse-owned block, so a write can be found and removed exactly."""

MARKER_END = "synapse-channel:adapter:end"
"""Sentinel closing the Synapse-owned block."""

FILE_MODE = "file"
"""Dedicated-file adapter: install replaces the whole file; uninstall deletes it."""

APPEND_MODE = "append"
"""Shared-file adapter: a marked block is added to a file the tool also owns."""

HOME_SCOPE = "home"
"""The adapter target is resolved under the user's home directory."""

PROJECT_SCOPE = "project"
"""The adapter target is resolved under the project working directory."""

KIMI_HOME_SCOPE = "kimi-home"
"""The adapter target is resolved under ``$KIMI_CODE_HOME`` or ``~/.kimi-code``."""


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
        Paths whose existence also marks the tool installed, relative to the
        applicable home root (including ``$KIMI_CODE_HOME`` for Kimi).
    target : str
        Path of the adapter file, relative to its scope.
    scope : str
        :data:`HOME_SCOPE`, :data:`PROJECT_SCOPE`, or :data:`KIMI_HOME_SCOPE` — what
        ``target`` is relative to.
    mode : str
        :data:`FILE_MODE` (Synapse owns the whole file) or :data:`APPEND_MODE`
        (a marked block inside a file the tool also uses).
    comment : str
        Marker comment style: ``"html"`` (``<!-- … -->``), ``"hash"`` (``# …``), or
        ``"skill"`` (Kimi SKILL.md) / ``"grok-skill"`` (Grok SKILL.md), whose
        frontmatter schemas intentionally differ.
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
    AdapterTool(
        key="grok",
        label="Grok Build TUI",
        binaries=("grok",),
        detect_paths=(".grok",),
        target=".grok/skills/synapse/SKILL.md",
        scope=HOME_SCOPE,
        mode=FILE_MODE,
        comment="grok-skill",
    ),
    AdapterTool(
        key="kimi",
        label="Kimi Code",
        binaries=("kimi",),
        detect_paths=("",),
        target="skills/synapse/SKILL.md",
        scope=KIMI_HOME_SCOPE,
        mode=FILE_MODE,
        comment="skill",
    ),
    AdapterTool(
        key="kimi-project",
        label="Kimi Code (project skill)",
        binaries=(),
        detect_paths=(),
        target=".kimi-code/skills/synapse/SKILL.md",
        scope=PROJECT_SCOPE,
        mode=FILE_MODE,
        comment="skill",
    ),
)
"""The tools the adapter kit can wire, surveyed against per-tool config conventions."""

_BY_KEY = {tool.key: tool for tool in CATALOGUE}


def tool_for(key: str) -> AdapterTool:
    """Return the catalogue tool for ``key``, raising :class:`KeyError` if unknown."""
    return _BY_KEY[key.strip().lower()]


def resolve_root(
    tool: AdapterTool,
    *,
    home: Path,
    project: Path,
    environ: Mapping[str, str] | None,
) -> Path:
    """Resolve a catalogue scope without touching the filesystem.

    Parameters
    ----------
    tool : AdapterTool
        Catalogue entry whose scope selects the configuration root.
    home, project : pathlib.Path
        Explicit home and workspace roots.
    environ : Mapping[str, str] or None
        Kimi path overrides, or None to use the current process environment.

    Returns
    -------
    pathlib.Path
        Selected home, workspace or Kimi configuration root.

    Raises
    ------
    ValueError
        The entry declares an unsupported scope.
    """
    if tool.scope == HOME_SCOPE:
        return home
    if tool.scope == PROJECT_SCOPE:
        return project
    if tool.scope == KIMI_HOME_SCOPE:
        values = os.environ if environ is None else environ
        configured = values.get("KIMI_CODE_HOME", "").strip()
        if configured:
            return Path(os.path.abspath(Path(configured).expanduser()))
        return home / ".kimi-code"
    raise ValueError(f"unknown adapter scope {tool.scope!r} for {tool.key!r}")


def detect_installed(
    tool: AdapterTool,
    *,
    home: Path,
    which: Callable[[str], str | None],
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether ``tool`` looks installed: a binary on ``PATH`` or a config dir present."""
    if any(which(binary) for binary in tool.binaries):
        return True
    root = resolve_root(tool, home=home, project=home, environ=environ)
    return any((root / path).exists() for path in tool.detect_paths)


def resolve_target(
    tool: AdapterTool,
    *,
    home: Path,
    project: Path,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the absolute adapter-file path for ``tool`` under its scope."""
    root = resolve_root(tool, home=home, project=project, environ=environ)
    return root / tool.target


def _contract_body(*, identity: str, hub_uri: str) -> str:
    """Render the format-agnostic claim-safety contract carried by every adapter."""
    return (
        "## Synapse coordination (claim-safety)\n\n"
        "This workspace uses Synapse to refuse overlapping live file claims.\n\n"
        "- **Claim before edit:** run `synapse git-claim <task-id> --paths <path>` (or a\n"
        "  semantic claim) before editing. Require an exact grant or confirmed live lease;\n"
        "  denial or unknown outcome does not permit editing.\n"
        "- **Release on commit:** select `--auto-release-on commit` for that workflow.\n"
        "  Installed git hooks request release only for matching owner/worktree/branch\n"
        "  and trigger. Hook exit 0 does not confirm release; verify uncertain outcomes.\n"
        f"- **Reach the hub:** identity `{identity}`, hub `{hub_uri}`.\n"
        "- **Enforcement boundary:** this file declares conventions, not host adoption or\n"
        "  a connected receiver. Provider hooks cover only their documented tools; the\n"
        "  staged Git gate is a separate check, not a filesystem or process sandbox.\n"
        "- Coordination safety only — this carries no persona, workflow, or model behaviour.\n"
    )


def render_block(tool: AdapterTool, *, identity: str, hub_uri: str) -> str:
    """Render the marker-wrapped adapter block for ``tool`` in its comment style."""
    body = _contract_body(identity=identity, hub_uri=hub_uri)
    if tool.comment == "grok-skill":
        return (
            "---\n"
            "name: synapse\n"
            "description: "
            "Synapse coordination rules — claim before edit, release on commit, reach the hub.\n"
            "when-to-use: "
            "Always, before modifying files or making commitments in a Synapse workspace.\n"
            "---\n\n"
            f"<!-- {MARKER_BEGIN} -->\n{body}<!-- {MARKER_END} -->\n"
        )
    if tool.comment == "skill":
        return (
            "---\n"
            "name: synapse\n"
            "description: "
            "Synapse coordination rules — claim before edit, release on commit, reach the hub.\n"
            "type: prompt\n"
            "whenToUse: "
            "Always, before modifying files or making commitments in a Synapse workspace.\n"
            "disableModelInvocation: false\n"
            "---\n\n"
            f"<!-- {MARKER_BEGIN} -->\n{body}<!-- {MARKER_END} -->\n"
        )
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

    This pure transformation does not authorize a filesystem mutation; the CLI
    uses :mod:`synapse_channel.adapter_custody` to establish exact-byte ownership.
    In :data:`FILE_MODE` the caller owns the whole file, so the planned content is
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

    This pure transformation assumes the caller already owns the selected bytes;
    markers alone do not authorize deletion. Runtime commands use the custody
    journal to preserve originals and refuse changed content.
    A :data:`FILE_MODE` adapter owns its file, so uninstall deletes it (``None``). An
    :data:`APPEND_MODE` adapter only strips its own marked block, leaving the rest of
    the tool's file intact.
    """
    if mode == FILE_MODE:
        return None
    return strip_block(existing)

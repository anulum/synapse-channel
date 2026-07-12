<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Cross-agent adapter kits design

Synapse coordinates whichever agents an operator already runs. The adapter kits
are the thin, claim-aware glue that wires a specific coding tool — Claude Code,
Codex, Gemini CLI, Grok, Kimi Code, Cursor, Aider, Copilot, and the Python
orchestration frameworks — into the hub so that tool claims its file scope
before editing and releases it on commit, without Synapse pretending to be that
tool or shipping a persona library.

This document began as the design contract for the adapter installer. The native
editor/CLI installer is now shipped; the contract and boundaries remain here so
an adapter never does more than carry claim-safety into a tool's own conventions.

## Runtime status

These wiring surfaces are implemented and are what an adapter kit composes — it
adds no new coordination primitive:

- **`synapse git-init`** installs claim-aware git hooks and writes the local
  `.synapse/` conventions guide before agents edit files.
- **`synapse git-hook` / `git-release`** auto-release a commit's branch-scoped
  claims, so "release on commit" already works for any tool that commits.
- **`synapse worker-session`** launches a provider command with identity env and
  a wake sidecar; **`agent-tmux` / `codex-tmux`** wake a terminal-agent session
  from hub messages; **`shell-hook` / `install-shell-hook`** auto-arm fresh
  terminals and provider CLIs.
- **`synapse mcp`** serves the hub to MCP-capable agents over stdio, and the
  **A2A bridge** exposes an Agent Card and HTTP+JSON endpoint.
- Typed **[Go](go-client.md)** and **[TypeScript/JavaScript](js-client.md)**
  clients speak the wire protocol directly.

The single `synapse adapters` step that detects the coding tools installed on a
machine and writes a thin claim-aware adapter into each tool's native configuration
location is **now shipped** for editor and CLI agents (`synapse_channel.adapters`
plus `cli_adapters`). The Python-framework client shims remain the documented
thin-client pattern over the existing client.

## The adapter contract

Every adapter, whatever the tool, carries the same small contract and nothing
more:

- **Claim before edit** — the tool is told to claim its file scope (`synapse
  git-claim` or a semantic claim) before modifying files, and to treat a denied
  claim as a stop.
- **Release on commit** — handled by the installed git hooks; the adapter only
  points the tool at `synapse git-init` so the hooks exist.
- **Reach the hub** — the adapter records the identity to use and the hub URI, so
  the tool's session is addressable and present.
- **Advisory, not behavioural** — the adapter conveys claim-safety conventions
  into the tool's own rules format; it does not inject a persona, a workflow, or
  model instructions beyond coordination safety.

## Two adapter shapes

Tools fall into two shapes, and the kit needs both:

1. **Editor and CLI agents** take a native config or rules file. The adapter
   writes a small claim-aware conventions file in each tool's own format and
   location. Install targets (surveyed against the cross-tool install map in
   *The Agency*, MIT — see Prior art):

   | Tool | Target | Format |
   | --- | --- | --- |
   | Claude Code | `~/.claude/` (or project `.claude/`) | Markdown |
   | Codex | `~/.codex/` | TOML |
   | Grok | `~/.grok/skills/synapse/SKILL.md` | Agent Skill (kebab-case frontmatter) |
   | Kimi Code | `$KIMI_CODE_HOME/skills/synapse/SKILL.md` (default `~/.kimi-code/skills/synapse/SKILL.md`) or project `.kimi-code/skills/synapse/SKILL.md` | Agent Skill |
   | Cursor | `.cursor/rules/synapse.mdc` | Cursor `.mdc` |
   | Aider | append to `./CONVENTIONS.md` | Markdown |
   | GitHub Copilot | `~/.github/` | Markdown |
   | Windsurf | `./.windsurfrules` | single file |
   | Gemini CLI / Qwen / OpenCode | tool agent dir | Markdown |

   Detection mirrors a tool's own footprint: a binary on `PATH` (`command -v`) or
   a config directory present (`~/.claude`, `~/.cursor`). Paths are overridable by
   flag and environment variable, never hardcoded-only.

2. **Python orchestration frameworks** (CrewAI, LangGraph, AutoGen, OpenDevin) do
   not take a config file; they take a **thin client shim**. The adapter is a
   small importable helper built on the existing `synapse_channel` client that an
   integration calls to claim a file scope before a tool step and release it
   after — a few lines, not a framework. Where a framework cannot express a claim
   boundary, the kit says so rather than faking support.

## `synapse adapters` surface

The command set is read-first and reversible:

- `synapse adapters list [TOOL ...]` — detect installed tools and print, for each,
  whether an adapter is installed and where it would be written. Detection only;
  writes nothing.
- `synapse adapters install [TOOL ...] [--identity ID] [--uri URI] [--dry-run]` —
  write the claim-aware adapter for detected (or named) tools; `--dry-run` prints the
  planned writes. Each write is idempotent and marker-wrapped so `uninstall` removes
  exactly what was added; re-installing replaces rather than duplicates.
- `synapse adapters uninstall [TOOL ...]` — remove only Synapse-written adapter
  content (delete a dedicated adapter file, or strip the marked block from a shared
  one), leaving the tool's other configuration untouched.

```bash
synapse adapters list                       # who is installed and where
synapse adapters install --identity proj/me # wire every detected tool
synapse adapters install grok --identity proj/grok # user-level Grok skill
synapse adapters install kimi-project --identity proj/me # explicit project skill
synapse adapters install kimi --identity proj/me --with-hook # skill + native edit guard
synapse adapters uninstall cursor           # remove just one
```

Two write shapes follow each tool's convention: a **dedicated file** Synapse owns
(Claude Code, Codex, Gemini CLI, Grok, Kimi Code, Cursor, Copilot — uninstall
deletes it) or a **marked block appended** to a file the tool also uses (Aider
`CONVENTIONS.md`, Windsurf `.windsurfrules` — uninstall strips only the block).
`--home`/`--project` override the roots; tool detection is a binary on `PATH` or
a config directory. Grok and Kimi deliberately use different Agent Skill
frontmatter because their installed 0.2.93 and 0.23.3 contracts differ.

Kimi exposes both official skill scopes. `kimi` installs the detected user-level
skill under `$KIMI_CODE_HOME/skills/` (default `~/.kimi-code/skills/`);
`kimi-project` is explicit-only and installs under the current project's
`.kimi-code/skills/`. Kimi resolves scopes in the documented order
**Project > User > Extra > Built-in**, so a project-specific identity and hub
override the user default. Skill installation itself does not modify
`config.toml`. The separate opt-in `--with-hook` flag installs the native
`PreToolUse` claim guard once even when both Kimi skill scopes are selected.
`--home` overrides and isolates the default user root for tests; otherwise both
the user Skill and hook config respect `$KIMI_CODE_HOME`.

## Prior art

The per-tool install-target conventions (which directory and format each editor or
CLI agent reads) were surveyed against *The Agency*
(`github.com/msitarzewski/agency-agents`, MIT), an agent-definition and
cross-tool deployment project. Synapse adapts only the install-target survey — the
idea of detecting installed tools and writing into each tool's native location —
not its agent personas or content. Synapse remains persona-neutral: it ships
coordination glue, not a roster.

## Boundaries

The editor and CLI installer is **shipped**; the Python-framework client shims are
not, and stay the documented thin-client pattern rather than a wrapper. The design is
deliberately narrow.

- Adapters are **thin and claim-aware only**: they carry "claim before edit,
  release on commit, reach the hub" into a tool's own conventions. They do not
  inject personas, workflows, or model behaviour.
- Synapse stays **persona-neutral and framework-neutral**: it is the coordination
  layer beside coding tools and frameworks, not a replacement for them and not an
  agent library.
- Every write is **opt-in, local, attributed, and reversible**: nothing is
  installed without an explicit `adapters install`, and `uninstall` removes
  exactly what was added.
- The kit **claims no support it cannot honour**: a framework that cannot express
  a claim boundary is documented as unsupported rather than wrapped in a shim that
  pretends.
- Adapters add **no new coordination primitive**: claims, releases, presence, and
  the hub already exist; the kit only routes existing tools to them.

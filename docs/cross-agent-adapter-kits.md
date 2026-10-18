<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Cross-agent adapter kits design

The adapter installer writes claim conventions for named coding tools. A written
file is not proof that the host loads it, follows those conventions, connects to
the hub or enforces edits. Exact host integrations and their qualified boundaries
are listed in the [integration catalogue](integration-catalog.md). Python
framework shims remain a design pattern, not shipped adapters.

This document began as the design contract for the adapter installer. The native
editor/CLI installer is now shipped; the contract and boundaries remain here so
an adapter never does more than carry claim-safety into a tool's own conventions.

## Runtime status

These wiring surfaces are implemented and are what an adapter kit composes — it
adds no new coordination primitive:

- **`synapse git-init`** installs claim-aware git hooks and writes the local
  `.synapse/` conventions guide before agents edit files.
- **`synapse git-hook` / `git-release`** request release of claims matching the
  exact owner, worktree, branch and configured trigger. Select
  `--auto-release-on commit` for commit closeout; the default Git claim trigger
  is `merge`. The non-blocking hook's exit `0` does not confirm hub release.
- **`synapse worker-session`** launches a provider command with identity env and
  a wake sidecar; **`agent-tmux` / `codex-tmux`** wake a terminal-agent session
  from hub messages; **`shell-hook` / `install-shell-hook`** auto-arm fresh
  terminals and provider CLIs.
- **`synapse mcp`** serves the hub to MCP-capable agents over stdio, and the
  **A2A bridge** exposes an Agent Card and HTTP+JSON endpoint.
- The typed **[TypeScript/JavaScript](js-client.md)** client implements WebSocket
  messages. The **[Go](go-client.md)** client reads HTTP JSON surfaces and does
  not implement WebSocket claims, chat, release or presence mutations.

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
- **Configured release** — the convention points to installed Git hooks and
  the matching release trigger; actual release must be confirmed separately.
- **Reach the hub** — the adapter records the identity to use and the hub URI, so
  a compatible session can load that configuration and connect. Creating the
  instruction file alone does not make the tool adopt it or create online presence.
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
   | Claude Code | `~/.claude/synapse.md` | Markdown convention file |
   | Codex | `~/.codex/synapse.md` | Markdown convention file |
   | Grok | `~/.grok/skills/synapse/SKILL.md` | Agent Skill (kebab-case frontmatter) |
   | Kimi Code | `$KIMI_CODE_HOME/skills/synapse/SKILL.md` (default `~/.kimi-code/skills/synapse/SKILL.md`) or project `.kimi-code/skills/synapse/SKILL.md` | Agent Skill |
   | Cursor | `.cursor/rules/synapse.mdc` | Cursor `.mdc` |
   | Aider | append to `./CONVENTIONS.md` | Markdown |
   | GitHub Copilot | `~/.github/synapse.md` | Markdown convention file |
   | Windsurf | `./.windsurfrules` | single file |
   | Gemini CLI | `~/.gemini/synapse.md` | Markdown convention file |

   These are this installer's targets, not a universal host-autoload contract.
   Qwen and OpenCode are not entries in this generic installer; consult the
   integration catalogue for separate surfaces.

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
- `synapse adapters install [TOOL ...] [--identity ID] [--uri URI] [--dry-run] [--adopt-existing]` —
  write the claim-aware adapter for detected (or named) tools; `--dry-run` prints the
  planned writes without creating parents or custody state. Installation refuses
  an existing unowned dedicated file. Explicit `--adopt-existing` preserves its
  original UTF-8 bytes before replacement, including complete skill frontmatter.
  Re-installation updates recorded bytes without duplicating an append block.
- `synapse adapters uninstall [TOOL ...]` — remove only Synapse-written adapter
  content: restore an adopted dedicated file or remove a newly created one.
  Shared files retain every byte outside the recorded segment, including later
  additions, CRLF and trailing whitespace. Modified dedicated files and edited
  or duplicated append blocks are preserved with a refusal.
- `synapse adapters recover [TOOL ...]` — settle a prepared transaction only when
  the target matches its recorded before or after bytes. Recovery does not replay
  a write or overwrite a third party's changes.

```bash
synapse adapters list                       # who is installed and where
synapse adapters install --identity proj/me # wire every detected tool
synapse adapters install grok --identity proj/grok # user-level Grok skill
synapse adapters install kimi-project --identity proj/me # explicit project skill
synapse adapters install kimi --identity proj/me --with-hook # skill + native edit guard
synapse adapters uninstall cursor           # remove just one
synapse adapters install codex --adopt-existing # preserve an existing dedicated file first
synapse adapters recover codex              # reconcile a previous interrupted write
```

Two write shapes follow each tool's convention: a **dedicated file** with recorded
ownership (Claude Code, Codex, Gemini CLI, Grok, Kimi Code, Cursor, Copilot) or an
**exact block appended** to a file the tool also uses (Aider `CONVENTIONS.md`,
Windsurf `.windsurfrules`). A marker alone never grants ownership. An existing
legacy dedicated adapter can be explicitly adopted; legacy append blocks require
manual merging before installation because their original outside bytes are unknown.
`--home`/`--project` override the roots; tool detection is a binary on `PATH` or
a config directory.
Grok and Kimi use different Agent Skill frontmatter, derived from the tested
0.2.93 and 0.23.3 contracts respectively; those are not claims about today's
installed versions.

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

### Local custody and recovery

Each target has a private adjacent `.<filename>.synapse-custody/` directory with
an advisory lock and `custody.sqlite3`. Keep it with the target: it contains the
single retained original and any pending before/after intent. It is recovery
state, not a disposable build cache. No backup history accumulates. The installer
accepts owner-controlled regular UTF-8 files up to 1 MiB, refuses symbolic links
below the selected root and multiple hard links, and rejects group/other-writable
targets. Newly created state is owner-only; existing target read permissions are
preserved on replacement. An explicitly selected root resolves its own existing
ancestors before validating all components below it.

The intent is committed before atomic replacement or removal; file and directory
syncs are requested where supported. Recovery retains conflicting target bytes
and pending intent for inspection. Losing or corrupting custody state does not
authorize marker-based deletion. A dry run inspects existing state without
settling pending intent. Multi-tool commands and the separate Kimi hook update
are individual transactions, so an earlier successful target remains installed
if a later target refuses.

The lock coordinates cooperating installers on one host. Cross-host filesystem
locks, hostile same-account writers and measured power-loss durability are outside
this contract. Native Windows qualification is tracked separately; Linux results
do not establish Windows ACL or replacement behavior.

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
- Every write is **opt-in and local**. Exact-byte custody preserves adopted
  originals and outside text. Changed or ambiguous owned content requires
  operator inspection rather than automatic replacement or deletion.
- The kit **claims no support it cannot honour**: a framework that cannot express
  a claim boundary is documented as unsupported rather than wrapped in a shim that
  pretends.
- Adapters add **no new coordination primitive**: claims, releases, presence, and
  the hub already exist; the kit only routes existing tools to them.

A Git claim timeout is an **unknown outcome** (exit `3`), not proof of denial. Use `--confirm-only` with the original identity and scope to verify an exact live lease without replaying a mutation. [Claim recovery](git-claims.md#claim-outcomes-and-recovery).

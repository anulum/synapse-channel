<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — provider mutation claim hooks
-->

# Provider mutation claim hooks

Synapse can stop a supported native mutation call before it runs unless the
configured identity owns the required live claim. Exact file tools require path
coverage for every target. Intercepted shell tools require an editable claim for
the complete worktree because arbitrary command text has no trustworthy declared
write set. Synapse never guesses write paths by parsing shell text.

| Provider | Covered native tool | Path source | Symbol-claim pre-edit use | Recipe format |
|---|---|---|---|---|
| Claude Code | `Edit`, `Write`; `Bash` | absolute `tool_input.file_path`; whole worktree for `Bash` | `Edit` only | `settings.json` fragment |
| Codex | `apply_patch` (matcher aliases `Edit|Write`); intercepted `Bash` | every add, update, delete, and move path in `tool_input.command`; whole worktree for `Bash` | no; patch payload requires whole-file claims | `hooks.json` fragment |
| Gemini CLI | `replace`, `write_file`, `run_shell_command` on the native `BeforeTool` event | relative or absolute `tool_input.file_path`; whole worktree for shell | `replace` only | `settings.json` `hooks` fragment |
| Grok | `search_replace` (plus `Edit` / `Write` / `MultiEdit` compatibility aliases and the older `write` spelling), `run_terminal_command` | relative or absolute `toolInput.path`; whole worktree for terminal commands | `search_replace`, `Edit`, and `MultiEdit` | global `~/.grok/hooks/*.json` fragment |
| Kimi Code | `Edit`, `Write`; `Bash` | relative or absolute `tool_input.path`; whole worktree for `Bash` | `Edit` only | `config.toml` fragment |
| OpenCode | `edit`, `write`, `apply_patch`, `bash` on `tool.execute.before` | `args.filePath`, or every add/update/delete/move path in `args.patchText`; whole worktree for `bash` | `edit` only | owned project/global plugin plus MCP config |

The decision requires the exact worktree, branch, path coverage, editable task
state, and unambiguous owner. A missing claim, competing owner, malformed event,
invalid Git context, stale state, unavailable hub, or query timeout produces the
provider's structured deny response. Successful checks print nothing and leave
the provider's ordinary permission flow unchanged.

A precise edit tool may provisionally use a `.synapse-symbol` claim for its
physical source file. A whole-file writer or patch tool still requires a literal
whole-file or parent claim because its payload cannot be proven symbol-bounded
before execution. If another owner holds any sibling symbol claim for the same
source in the same worktree and branch, the pre-edit decision is ambiguous and
denies both owners. Parallel sibling-symbol work therefore belongs in isolated
Git worktrees; the staged index gate below remains the authoritative proof of
which symbol the resulting change actually touched.

## Getting started

Claim the intended paths first:

```bash
synapse git-claim EDIT-AUTH \
  --paths src/auth \
  --name my-repo/codex \
  --auto-release-on manual
```

For a shell call, take an explicit whole-worktree claim by omitting `--paths`:

```bash
synapse git-claim SHELL-AUTH \
  --name my-repo/codex \
  --auto-release-on manual
```

That claim is intentionally exclusive. If the worktree contains any active
claim for another owner, the shell guard denies the call. Prefer native file
tools with bounded path claims whenever a whole-worktree lease is unnecessary.

Print the recipe for the provider you run:

```bash
synapse adapters claude-claim-hook \
  --identity my-repo/claude \
  --print-config

synapse adapters codex-claim-hook \
  --identity my-repo/codex \
  --print-config

synapse adapters gemini-claim-hook \
  --identity my-repo/gemini \
  --print-config

synapse adapters grok-claim-hook \
  --identity my-repo/grok \
  --print-config

synapse adapters kimi-claim-hook \
  --identity my-repo/kimi \
  --print-config

synapse adapters opencode print-config \
  --identity my-repo/opencode \
  --asset plugin
```

With `--print-config`, these commands only print mergeable fragments and never
write provider configuration. Merge the Claude fragment into
`.claude/settings.json`, the Codex fragment into `.codex/hooks.json`, the Gemini
fragment into the `hooks` key of `.gemini/settings.json`, save the Grok fragment
as one global `~/.grok/hooks/*.json` file, or merge the Kimi fragment into
`$KIMI_CODE_HOME/config.toml` (default `~/.kimi-code/config.toml`), then use the
provider's normal hook inspection flow. Codex requires operators to review and
trust a new or changed non-managed hook. Gemini's hook `timeout` field is in
milliseconds, and the printed fragment already uses that unit; Gemini also
refuses project-scope hooks inside untrusted folders. Grok global hooks are
always trusted; a project `.grok/hooks/` copy would require folder trust.

OpenCode has a reversible project/global installer that owns only its marked
plugin and marked `mcp.synapse` entry:

```bash
synapse adapters opencode install \
  --scope project \
  --project . \
  --identity my-repo/opencode

synapse adapters opencode status --scope project --project .
synapse adapters opencode uninstall --scope project --project .
```

The adapter refuses unowned collisions, symlinks, non-regular or foreign-owned
files, unsafe modes, files changed before atomic replacement, oversized input,
and JSONC rewrites. See the complete [OpenCode bridge](opencode.md).

Kimi also has an explicit reversible installer:

```bash
synapse adapters kimi-claim-hook \
  --identity my-repo/kimi \
  --install-config

synapse adapters kimi-claim-hook --uninstall-config

# Install the user Skill and native hook together:
synapse adapters install kimi --identity my-repo/kimi --with-hook
```

The installer owns only the
`synapse-channel:kimi-hook:{begin,end}` block. It validates the surrounding TOML,
limits automatic edits to one MiB, rejects a final-component symlink or a config
owned by another user, preserves the existing mode, writes a private `0600` file
when creating one, and refuses a snapshot changed before replacement. It uses a
same-directory temporary file, fsync, and atomic replacement. Kimi's host-level
fail-open limitation still applies after installation.

For a secured hub, pass a file path rather than a token value:

```bash
synapse adapters codex-claim-hook \
  --identity my-repo/codex \
  --uri wss://hub.example/ws \
  --token-file /run/secrets/synapse-hub-token \
  --print-config
```

The generated command contains the absolute token-file path, not its contents.
The hook timeout always exceeds both bounded hub-query phases. Provider recipes
reject a per-phase deadline above 299 seconds so their complete query remains
inside the hosts' 600-second hook ceiling.

## What fail-closed means here

### Provider × fail-closed matrix (hook failure)

Synapse always converts **handled** failures into a structured deny on exit 0.
Hosts may still **fail open** when the hook process itself crashes, times out,
or returns non-JSON. That host residual is the fail-open matrix:

| Provider | Synapse handled failure | Host crash / timeout / bad JSON | Residual outside the covered hook path |
|---|---|---|---|
| Claude Code | deny on exit 0 for file tools and `Bash` | host-dependent; structured deny preferred | MCP, custom, and future write-capable tools |
| Codex | deny on exit 0 for `apply_patch` and intercepted `Bash` | host documents PreToolUse as guardrail | `unified_exec` interception is incomplete; MCP and future tools remain outside |
| Gemini CLI | structured `decision=deny` on exit 0, including `run_shell_command` | plain non-JSON exit 1 is a warning (**fail-open**) | MCP, custom, and future write-capable tools |
| Grok | structured `decision=deny` on exit 0, including `run_terminal_command` | host fail-open on timeout/crash/malformed | custom and future write-capable tools |
| Kimi Code | structured deny on exit 0, including `Bash` | host documents fail-open on crash/timeout | custom and future write-capable tools |
| OpenCode | plugin throws before covered mutation, including `bash` | helper crash/timeout → throw (**fail-closed** for covered tools) | custom, MCP, and future write-capable tools |

See also [SECURITY.md — known limitations](https://github.com/anulum/synapse-channel/blob/main/SECURITY.md#out-of-scope--known-limitations)
for the residual that commit-time `git-claim-check` covers independently.

The Synapse handler returns a valid deny object on exit zero for every handled
parse, Git, state, transport, timeout, and unexpected runtime failure. This
avoids provider conventions in which an ordinary non-zero error means
"continue." It does not turn a native hook into a filesystem sandbox.

- **Claude Code:** the matcher covers `Edit|Write|Bash`; `Bash` requires the
  whole-worktree claim. Custom and future write-capable tools remain outside.
- **Codex:** the hook validates every path named by `apply_patch`, including both
  sides of a move, and intercepts canonical `Bash` with the whole-worktree rule.
  Codex documents `PreToolUse` as a guardrail rather than a complete enforcement
  boundary: newer `unified_exec` interception is incomplete, and MCP or future
  tool paths may perform equivalent writes without this matcher.
- **Gemini CLI:** the guard speaks Gemini's native contract — the `BeforeTool`
  event, `replace`/`write_file`/`run_shell_command` tool names, and a top-level
  `{"decision": "deny", "reason": …}` blocking response — verified against the
  installed 0.47.0 bundle source. Shell requires the whole-worktree claim. MCP
  remains outside this matcher, and Gemini treats a plain non-JSON exit 1 as a
  non-blocking warning, so handled failures always emit structured deny on exit 0.
- **Grok:** the guard speaks the installed 0.2.101 contract — camelCase
  `PreToolUse` input, native `search_replace` path data, and top-level
  `{"decision": "deny", "reason": …}` output. Grok's host runner treats hook
  timeouts, crashes, and malformed output as fail-open, so Synapse bounds the
  query and converts every handled failure to explicit deny JSON on exit zero.
  `run_terminal_command` now uses the whole-worktree claim rule. Future
  write-capable tools outside the matcher remain outside this bounded guard.
- **Kimi Code:** the handler converts all failures it receives into structured
  denial on exit zero. Kimi documents its hook runner itself as fail-open if the
  process crashes or exceeds the host timeout. Synapse leaves headroom and catches
  runtime exceptions, but it cannot change that host-level behavior.
- **OpenCode:** the generated plugin accepts only an explicit allow response from
  the bounded helper process. A helper crash, timeout, excessive output,
  invalid UTF-8/JSON, ambiguous response, missing claim, or hub failure throws
  from `tool.execute.before` before `edit`, `write`, `apply_patch`, or `bash`
  runs. The plugin sends metadata only for `bash`; command text is neither parsed
  nor forwarded to the helper. Custom, MCP, and future tool names remain outside.

Do not describe these adapters as complete shell or operating-system isolation.
For a stricter deployment, combine them with provider sandboxing and deny
unneeded execution tools.

## Commit-time defense in depth

Install the Git gate even when a native edit hook is active:

```bash
synapse git-init --name my-repo/codex
synapse git-claim-check --staged --name my-repo/codex
```

The native hook stops covered edits early. The staged-path gate independently
checks every path before commit, including changes produced through a shell path
the host did not intercept or an external program. When a staged source has a semantic claim, it compares
`HEAD` with the Git index, maps both hunk sides through the local tree-sitter
parser, and checks the exact `.synapse-symbol` paths. Module-level edits,
add/delete/rename operations, unsupported or invalid syntax, ambiguous mappings,
and other incomplete evidence widen to the physical file and therefore require a
whole-file claim. A missing parser or unreadable index denies rather than
silently accepting the physical path. Neither gate grants a claim; operators and
agents must claim their scope explicitly.

## Source references

- [Codex hooks](https://developers.openai.com/codex/hooks)
- [Gemini CLI](https://github.com/google-gemini/gemini-cli) — the `BeforeTool`
  contract above was read from the installed 0.47.0 bundle source
- Grok 0.2.101 — the `PreToolUse`, matcher-alias, input, deny, timeout, and
  fail-open contracts above were read from the installed user guide
- [Kimi Code hooks](https://moonshotai.github.io/kimi-code/en/customization/hooks)
- [OpenCode plugins](https://opencode.ai/docs/plugins/) — native
  `tool.execute.before` contract pinned to 1.17.20
- [Git-native claims](git-claims.md)

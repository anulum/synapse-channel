<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — provider file-edit claim hooks
-->

# Provider file-edit claim hooks

Synapse can stop a supported native file-edit call before it runs unless the
configured identity owns a live claim for every target file. Claude Code, Codex,
and Kimi use different hook payloads, so Synapse keeps their wire adapters small
and sends all three through one claim decision engine.

| Provider | Covered native tool | Path source | Recipe format |
|---|---|---|---|
| Claude Code | `Edit`, `Write` | absolute `tool_input.file_path` | `settings.json` fragment |
| Codex | `apply_patch` (matcher aliases `Edit|Write`) | every add, update, delete, and move path in `tool_input.command` | `hooks.json` fragment |
| Kimi Code | `Edit`, `Write` | relative or absolute `tool_input.path` | `config.toml` fragment |

The decision requires the exact worktree, branch, path coverage, editable task
state, and unambiguous owner. A missing claim, competing owner, malformed event,
invalid Git context, stale state, unavailable hub, or query timeout produces a
structured `permissionDecision: deny` response. Successful checks print nothing
and leave the provider's ordinary permission flow unchanged.

## Getting started

Claim the intended paths first:

```bash
synapse git-claim EDIT-AUTH \
  --paths src/auth \
  --name my-repo/codex \
  --auto-release-on manual
```

Print the recipe for the provider you run:

```bash
synapse adapters claude-claim-hook \
  --identity my-repo/claude \
  --print-config

synapse adapters codex-claim-hook \
  --identity my-repo/codex \
  --print-config

synapse adapters kimi-claim-hook \
  --identity my-repo/kimi \
  --print-config
```

With `--print-config`, these commands only print mergeable fragments and never
write provider configuration. Merge the Claude fragment into
`.claude/settings.json`, the Codex fragment into `.codex/hooks.json`, or the Kimi
fragment into `$KIMI_CODE_HOME/config.toml` (default
`~/.kimi-code/config.toml`), then use the provider's normal hook inspection flow.
Codex requires operators to review and trust a new or changed non-managed hook.

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

The Synapse handler returns a valid deny object on exit zero for every handled
parse, Git, state, transport, timeout, and unexpected runtime failure. This
avoids provider conventions in which an ordinary non-zero error means
"continue." It does not turn a native hook into a filesystem sandbox.

- **Claude Code:** the released integration covers `Edit|Write`. Claude `Bash`
  commands remain outside this hook.
- **Codex:** the hook validates every path named by `apply_patch`, including both
  sides of a move. Codex documents `PreToolUse` as a guardrail rather than a
  complete enforcement boundary: alternate shell, `unified_exec`, MCP, or future
  tool paths may perform equivalent writes without this matcher.
- **Kimi Code:** the handler converts all failures it receives into structured
  denial on exit zero. Kimi documents its hook runner itself as fail-open if the
  process crashes or exceeds the host timeout. Synapse leaves headroom and catches
  runtime exceptions, but it cannot change that host-level behavior.

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
checks every path before commit, including changes produced through an unguarded
shell or external program. Neither gate grants a claim; operators and agents must
claim their scope explicitly.

## Source references

- [Codex hooks](https://developers.openai.com/codex/hooks)
- [Kimi Code hooks](https://moonshotai.github.io/kimi-code/en/customization/hooks)
- [Git-native claims](git-claims.md)

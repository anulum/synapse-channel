<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Claude Code plugin

The optional `synapse-channel` Claude Code plugin packages the existing
Synapse MCP bridge and the `PreToolUse` claim guard. The plugin is versioned
separately at **0.1.0**. It requires Synapse Channel **0.99.26 or newer** with
the `mcp` extra; this package was validated with Claude Code **2.1.273**.
Other host versions need their own validation before installation. The plugin
adds one MCP server and one `Edit|Write|Bash` hook. It does not grant permissions
or change Claude Code's model, account, or project settings.

## Inspect and preview

```bash
synapse adapters claude-plugin inspect
synapse adapters claude-plugin dry-run \
  --identity MY-PROJECT/claude --uri ws://127.0.0.1:8876
```

Use `--config-root DIR` or `CLAUDE_CONFIG_DIR` for an isolated profile.
`inspect` reads only the plugin target. `dry-run` validates the requested
configuration and reports the planned target without writing it. It accepts
`--operation install|upgrade|uninstall` to preview each action.

## Install and verify

```bash
synapse adapters claude-plugin install \
  --identity MY-PROJECT/claude --uri ws://127.0.0.1:8876
synapse adapters claude-plugin diagnose
claude plugin list --json
claude plugin details synapse-channel@skills-dir
```

For an authenticated hub, add `--token-file /owner-only/path/hub.token`.
The file must satisfy Synapse's owner-only secret-file check. Its contents are
read only by the MCP and hook processes; they are never written into the
plugin, command arguments or diagnostic output. The selected `synapse` binary
must support `mcp --token-file`. Use `--synapse-bin` to select an exact installed
binary and `--claude-bin` to select the host validator.

Installation writes only `skills/synapse-channel/` under the chosen Claude
profile. It refuses a foreign or modified target. The installer stages the
complete package and runs the actual host's strict plugin validator before
promoting it. The metadata marker records checksums of the plugin's four
managed files; it stores no credential. `diagnose` checks that marker and
reruns host validation. In a new Claude session, the plugin appears as
`synapse-channel@skills-dir`. The host may ask for MCP server approval.

The MCP server uses `synapse mcp --name` with the exact identity and exposes
the hub board and claims. The hook invokes the existing Synapse Claude claim
guard for `Edit`, `Write`, and `Bash`. Edit or Write without a covering current
claim is denied; unsupported shell effects fail closed under the existing
guard policy. This is a cooperative hook, not operating-system-enforced write
custody. [Git-native claims](git-claims.md) describes scope and release.

## Upgrade or remove

```bash
synapse adapters claude-plugin upgrade \
  --identity MY-PROJECT/claude --uri ws://127.0.0.1:8876
synapse adapters claude-plugin uninstall
```

Upgrade validates a newly staged plugin before replacing the owned package.
Uninstall removes only the intact, checksummed plugin directory. Both refuse
unexpected or edited files so user work remains available for manual review.
Neither command rewrites `settings.json`, other skills, plugins, or project
configuration. Restart Claude Code or use `/reload-plugins` to pick up a
changed MCP server or hook. A local install does not publish to a marketplace.

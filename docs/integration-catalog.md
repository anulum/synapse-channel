<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Host integration catalog

`synapse integrations list` reports reviewed host versions and the operations
Synapse can perform. It does not install hosts or promote a newly discovered
version. `observed_version` is the selected executable on this machine;
`version_accepted` is true only for the exact version exercised by the
corresponding host contract. The vendor watch records newer releases
separately from this acceptance catalog.

| Host | Reviewed version | Lifecycle | Rollback evidence |
|---|---:|---|---|
| Claude Code | 2.1.280 | Native plugin: inspect, install, diagnose, uninstall | 2.1.278 |
| Codex CLI | 0.156.0 | Manual local stdio MCP: inspect, install, diagnose, uninstall | None validated |
| Pi | 0.87.1 | Bound participant: inspect, offline RPC diagnose | 0.86.0 |
| OpenCode | 1.18.32 | Native adapter: inspect, install, diagnose, uninstall | 1.18.31 |
| Gemini CLI | None | Inspect only; packaged extension unsupported | None |
| Claude Desktop | None | Inspect only; package unsupported | None |

Codex's accepted operation is a local stdio MCP server. It does not claim a
native edit guard, a model-driven tool call, remote MCP or a published plugin.
Pi's guard needs the exact task, claim epoch and session for each turn; installing
it globally would block unrelated sessions. Gemini CLI and Claude Desktop have
no validated Synapse package in this release. `install`, `diagnose` or
`uninstall` returns an explicit unsupported result for those hosts.

Use `--profile-root` to keep a host's settings isolated. The root means
`CLAUDE_CONFIG_DIR` for Claude Code, `CODEX_HOME` for Codex, the parent of
`opencode/` for OpenCode, and `PI_CODING_AGENT_DIR` for Pi. Without an override,
the host's corresponding environment variable or normal user directory is used.
No action edits a different host's profile.

```bash
synapse integrations list
synapse integrations inspect codex-cli --profile-root /tmp/isolated-codex
synapse integrations install codex-cli \
  --profile-root /tmp/isolated-codex --host-bin /path/to/codex-0.156.0 \
  --synapse-bin /path/to/synapse --identity PROJECT/seat \
  --uri ws://127.0.0.1:8876
synapse integrations diagnose codex-cli \
  --profile-root /tmp/isolated-codex --host-bin /path/to/codex-0.156.0
synapse integrations uninstall codex-cli \
  --profile-root /tmp/isolated-codex --host-bin /path/to/codex-0.156.0
```

Codex installation uses its own `mcp add` command and writes an owner-only
checksum marker for the resulting `mcp_servers.synapse` entry. A foreign or
changed entry is refused on uninstall; other MCP entries remain in place.
The selected Synapse executable must expose `mcp`. For an authenticated hub,
use an owner-only `--token-file`; token contents never enter the configuration.
The `--uri` value accepts a `ws://` or `wss://` hub endpoint without embedded
credentials, a query or a fragment; keep authentication in the token file.
The configured file path is visible to the host, so keep it outside the working
repository. Codex profile changes require a new session to load.

Claude Code delegates to the existing strict host plugin validator and
checksum-owned installer. OpenCode delegates to its existing owned MCP/plugin
adapter. Each rejects unreviewed host versions on install and diagnose. Their
native setup, claim boundaries and rollback instructions remain in the
[Claude plugin](claude-plugin.md) and [OpenCode](opencode.md) guides.

Pi diagnosis asks the exact offline RPC host for its registered extension
commands. It neither prompts the model nor starts a Synapse task:

```bash
synapse integrations diagnose pi \
  --host-bin integrations/pi/node_modules/.bin/pi \
  --profile-root /path/to/isolated-pi-agent \
  --pi-model ollama/LOCAL-MODEL \
  --pi-extension integrations/pi/index.ts
```

The profile needs a locally configured model so Pi can start, even though the
diagnostic sends no prompt. A loaded extension is only a configuration check;
the [Pi participant guide](pi.md) describes the real claim-bound turn. Host
profiles may contain private settings, so keep diagnostics and temporary
profiles owner-controlled. Host package publication, marketplace listings and
account setup are separate decisions.

The existing MCP Registry workflow and GitHub Action remain separate release
checklist items. Their hosted publication, listing visibility and exact release
artifact must be verified under C24/C25 after an authorised push; this local
catalog does not establish those results.

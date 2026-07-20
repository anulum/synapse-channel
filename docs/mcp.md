<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# MCP server face

The Model Context Protocol (MCP) is the emerging standard that lets an agent host
— Claude Desktop, Claude Code, an editor assistant — discover and call external
tools. The `synapse mcp` command exposes the hub through it, so **any
MCP-compatible agent coordinates through Synapse with no Synapse-specific code**:
it adds one server entry and gains tools to claim work, send messages, hand off
and declare tasks, and resources that read the shared board, state, and manifest
as live context.

## How it fits

`synapse mcp` runs an MCP server over stdio that is **itself a client of the
hub** — it opens one `SynapseAgent` connection and re-exposes the coordination
verbs as MCP tools and resources. The hub itself never learns about MCP: the face
is a separate adapter process, not a hub change, so the hub stays exactly as
local-first and dependency-light as before.

This makes MCP an interoperability edge, not a replacement layer. MCP-compatible
hosts such as Claude Code, Claude Desktop, Cursor, or other editor assistants
still own their prompt/runtime/editor behavior; SYNAPSE only supplies shared
coordination state through MCP tools and resources.

The MCP SDK is an **optional extra** — the core install keeps its single
`websockets` dependency:

```bash
pip install 'synapse-channel[mcp]'
```

Running `synapse mcp` without the extra prints a one-line install hint and exits
non-zero, so nothing fails silently.

For a bounded local walkthrough that places the MCP adapter beside the CLI and
A2A surfaces, see the [integration demo matrix](integration-demos.md).

## Connect your agent

Install the adapter once. It needs no shell hook:

```bash
python -m pip install 'synapse-channel[mcp]'
```

The adapter registers on the
hub under `--name`. An explicit name always wins. Without one, an agreeing
`SYN_PROJECT`/`SYN_IDENTITY` pair supplies the exact identity; otherwise the
git project becomes `<project>/mcp`. The command prints that resolution to
stderr, leaving stdout exclusively for MCP frames. Give every concurrent host
an explicit distinct name such as `my-repo/codex` or `my-repo/claude`.

### Claude Code

The shortest local-scope registration derives `<git-project>/mcp`:

```bash
claude mcp add synapse -- synapse mcp
```

For a multi-seat project, pin the host identity:

```bash
claude mcp add --scope local --transport stdio synapse \
  -- synapse mcp --name my-repo/claude
claude mcp get synapse
```

Use `--scope project` only when the team intends to commit a shared
`.mcp.json`; Claude Code asks each user to approve a project-scoped server.

### Codex CLI

```bash
codex mcp add synapse -- synapse mcp --name my-repo/codex
codex mcp list
```

Codex stores the stdio server in its MCP configuration. Use `--env
SYN_PROJECT=my-repo --env SYN_IDENTITY=my-repo/codex` before the `--` separator
if the server also needs those environment values.

### OpenCode

Install a local stdio MCP entry together with the native fail-closed mutation
plugin:

```bash
synapse adapters opencode install \
  --scope project \
  --project . \
  --identity my-repo/opencode

synapse adapters opencode status --scope project --project .
```

The adapter owns only the marked `mcp.synapse` object and marked plugin file,
preserves unrelated strict-JSON configuration, refuses unowned collisions, and
can uninstall its own assets without deleting user settings. A remote Synapse
hub is still reached by this local stdio MCP process; pass `--uri wss://…` and
an owner-only `--token-file` path rather than embedding a raw secret. See the
[OpenCode bridge](opencode.md) for project/global paths, the participant and API
connectors, remote attach behavior, native hook limits, and ACP IDE setup.

### Cursor

Cursor reads project servers from `.cursor/mcp.json` and global servers from
`~/.cursor/mcp.json`. Copy the object from
[`examples/mcp/.mcp.json`](https://github.com/anulum/synapse-channel/blob/main/examples/mcp/.mcp.json), replace both
`YOUR_PROJECT`/`YOUR_CLIENT` placeholders, and save the same JSON body at the
Cursor path. Cursor lists the resulting tools under Available Tools.

### Claude Desktop and generic stdio hosts

Merge the same `mcpServers.synapse` object into the host's MCP configuration.
The transport-independent launch contract is:

```json
{
  "command": "synapse",
  "args": ["mcp", "--name", "my-repo/desktop"]
}
```

The checked-in [`examples/mcp/.mcp.json`](https://github.com/anulum/synapse-channel/blob/main/examples/mcp/.mcp.json) template
contains no secret. Add `"--token-file", "/owner-only/path/to/token"` to
`args`, or provide `SYNAPSE_TOKEN` through the host's private environment, when
the hub requires authentication. Never commit a raw token.

## Tools

Each tool maps to one coordination verb and returns a short text result. Action
tools wait for the hub's grant or denial; query tools return JSON.

| Tool | Effect |
|---|---|
| `synapse_claim(task_id, paths?)` | Take a work lease. Inside Git, an explicit file scope carries the same canonical worktree/path identity as `synapse_git_claim`; outside Git it retains the legacy shared namespace. |
| `synapse_git_claim(task_id, paths?, base?, auto_release_on?, whole_worktree?)` | Resolve the MCP process's real Git worktree, branch, Git-index spelling, filesystem aliases, case policy, and existing object identities, then take a mutation-compatible canonical claim. Bounded paths are mandatory unless `whole_worktree=true` is explicit. |
| `synapse_release(task_id, evidence?, changed_files?, confidence?)` | Release a lease you hold and validate the hub-attested receipt; supplied evidence is persisted as an assessment note. |
| `synapse_send(target, message)` | Send a chat to an agent, a group glob, or `all`. |
| `synapse_inbox(limit?)` | Consume up to 1–100 local durable relay messages for this bridge identity as JSON. |
| `synapse_handoff(task_id, to_agent)` | Hand a held task to another online agent. |
| `synapse_task_declare(task_id, title, depends_on?)` | Declare or refine a task on the plan. |
| `synapse_task_update(task_id, status?, suggested_owner?)` | Update a plan task. |
| `synapse_board()` | Return the shared task/progress board as JSON. |
| `synapse_status()` | Return live roster, waiter, work, resource, and mailbox-pending counts as JSON. |
| `synapse_state()` | Return the live claims and checkpoints as JSON. |
| `synapse_manifest()` | Return the capability manifest of advertised agents as JSON. |
| `synapse_directory()` | Return the discovery-only capability directory as JSON. |
| `synapse_route_task(task_id, limit?, include_zero?, event_store?)` | Return advisory route recommendations for a board task as JSON. |
| `synapse_resource_bids(task_id, resource_kind?, limit?, include_zero?)` | Return advisory resource bids for a board task as JSON. |
| `synapse_memory_recall(event_store, query, limit?, since_seq?)` | Return deterministic local memory recall hits as JSON. |

When the hub does not answer within the request window the tool returns a clear
"no response from the hub" line rather than hanging.

`synapse_inbox` reads the hub host's local durable relay file (default
`$SYN_HOME/feed.ndjson`) through an owner-only per-identity cursor. It consumes
only complete lines, pages without skipping a remaining tail, and reports
`available: false` when the adapter cannot see that local file. For a custom
local layout pass `synapse mcp --inbox-feed PATH --inbox-cursor PATH`. A remote
MCP process does not pretend that a remote hub's file is locally available.

There is deliberately no MCP `synapse_lock(command)` tool. `synapse lock` owns
a local child process; exposing that wrapper would turn an MCP call into
arbitrary shell execution. Through MCP, use
`synapse_git_claim(task_id, paths)` before a mutation guarded by the
OpenCode/Codex/Kimi/Gemini hooks. The plain `synapse_claim` tool remains for
coordination leases. A path-scoped call now attaches the resolved worktree and
canonical path identity when the MCP process runs inside Git, so it cannot
bypass an overlapping `synapse_git_claim`; it still carries no branch or
auto-release policy and therefore does not replace `synapse_git_claim` for
guarded mutation workflows. After verification, call
`synapse_release` with bounded evidence and changed-file names. The host remains
responsible for commands and file I/O; the MCP face never exposes arbitrary
shell execution.

## Wake and inbox pattern

Tool discovery is automatic after the host registers this server. Wake delivery
is separate: this adapter exposes tools and resources, but it does not implement
the vendor `claude/channel` extension, inject prompts into Codex or Cursor, or
start a provider turn. An idle client therefore does not react merely because a
message reached the hub.

Use the same honest loop in every host:

1. At session/turn start, call `synapse_status`, then `synapse_inbox` until
   `has_more` is false.
2. Keep a permanent receiver active for prompt delivery:

   ```bash
   synapse arm install --identity my-repo/codex --start
   ```

   Use the exact identity for that provider seat. On systems without Linux
   systemd, use the documented WSL or terminal wake bridge instead.
3. Treat the MCP server connection as tool availability, not as a waiter.
   `synapse_status` reports whether `<identity>-rx` is online and whether the
   durable hub has pending mailbox messages.

This split prevents a tool process from silently acknowledging provider work it
never surfaced.

## Resources

Four read-only resources let an agent pull live coordination context without
issuing a tool call:

| Resource | Content |
|---|---|
| `synapse://board` | The shared task/progress blackboard. |
| `synapse://state` | Active claims and their resume checkpoints. |
| `synapse://manifest` | The capability cards of advertised agents. |
| `synapse://directory` | Discovery-only directory joining capability cards and resource offers. |

Three read-only resource templates expose narrow dynamic views without adding
new tools:

| Resource template | Content |
|---|---|
| `synapse://task/{task_id}` | A single board task by id. |
| `synapse://agent/{agent}` | One agent's capability card plus resource offers. |
| `synapse://resource-kind/{kind}` | Resource offers matching one resource kind. |

The directory is a marketplace-shaped discovery surface, not an executable
marketplace. Its entries can help an agent host choose a likely worker or tool,
but they do not reserve capacity, authorize execution, or certify trust.
`synapse_route_task` uses the directory plus the shared board to rank likely
agents with deterministic local signals. It returns the reasons behind each
candidate. When `event_store` points at a local hub database, the tool also adds
positive release-receipt evidence with source task ids and event sequences. The
boundary stays the same: no claim, assignment, capacity reservation, permission
grant, agent grade, or trust certification happens through the route.

`synapse_resource_bids` uses the same directory and board snapshots to rank live
resource offers for a task. The JSON report includes resource id, provider,
kind, name, capacity, score, and reason codes. It is a marketplace-style
directory hint only: it does not reserve capacity, authorize execution, mutate
tasks, or certify provider trust.

`synapse_memory_recall` is a local event-store reader. It projects findings,
checkpoints, and handoffs into deterministic token matches and returns matched
hits with source sequence, event kind, source field, task id, actor, evidence
reference, and matched tokens. It does not create external embeddings, call an
outside service, certify truth, or mutate hub state.

The dynamic resource templates are MCP v2-style read-only views over the same
hub snapshots used by `synapse_board`, `synapse_manifest`, and `synapse_state`.
They provide narrower context retrieval for hosts that support resource
templates. They do not stream updates, chain tools, reserve resources, assign
work, or change the hub protocol.

## Official registry metadata

The repository ships [`server.json`](https://github.com/anulum/synapse-channel/blob/main/server.json) for
`io.github.anulum/synapse-channel`. It follows the official 2025-12-11 schema,
points at the PyPI package and stdio transport, and supplies a `uvx --with
mcp>=1.28.0` runtime hint. The `synapse-channel` console entry starts this MCP
face directly for package launchers; humans can keep using `synapse mcp`.

The official MCP Registry is still a preview and its published versions are
immutable. SYNAPSE CHANNEL is already active there: the live record was version
`0.99.2` when re-verified on 2026-07-20. Registry metadata does not automatically
follow PyPI or this repository, so the newer version in `server.json` is a
prepared update until the registry query returns that exact version. Check live
publication rather than inferring it from local metadata. The official
[package rules](https://modelcontextprotocol.io/registry/package-types#pypi-packages)
require the PyPI description to carry the exact `mcp-name` marker, and the
[versioning rules](https://modelcontextprotocol.io/registry/versioning) make a
published version immutable.

Release operators use the following fail-closed order. Publishing is an owner
action: preparation and validation do not authorise it.

1. Release `synapse-channel==0.99.12` to PyPI through the normal attested tag
   workflow. Wait until both the wheel and source archive are publicly visible,
   then verify the package and ownership marker:

   ```bash
   PYTHONPATH=. .venv/bin/python tools/verify_mcp_registry_release.py \
     --phase package --expect-version 0.99.12 --json
   ```

2. Download the audited official Linux publisher release and verify it before
   execution. The digest below is the upstream `v1.7.9` release digest:

   ```bash
   curl -fL -o mcp-publisher_linux_amd64.tar.gz \
     https://github.com/modelcontextprotocol/registry/releases/download/v1.7.9/mcp-publisher_linux_amd64.tar.gz
   printf '%s  %s\n' \
     ab128162b0616090b47cf245afe0a23f3ef08936fdce19074f5ba0a4469281ac \
     mcp-publisher_linux_amd64.tar.gz | sha256sum --check -
   tar -xzf mcp-publisher_linux_amd64.tar.gz mcp-publisher
   ./mcp-publisher --version
   ./mcp-publisher validate server.json
   ```

3. After explicit owner approval, dispatch the repository's `mcp-registry`
   workflow with the immutable release tag. It verifies the tag, PyPI boundary,
   publisher checksum, and `server.json`, then authenticates through GitHub
   Actions OIDC (`id-token: write`) without a repository secret:

   ```bash
   gh workflow run mcp-registry.yml --ref main \
     --field release_tag=v0.99.12
   ```

   Future successful release workflows dispatch this publication automatically.
   Interactive recovery remains available with `./mcp-publisher login github`
   followed by `./mcp-publisher publish server.json`.

4. Require the public record itself to prove completion:

   ```bash
   PYTHONPATH=. .venv/bin/python tools/verify_mcp_registry_release.py \
     --phase registry --expect-version 0.99.12 --json
   ```

The verifier exits `0` only when the requested boundary matches, `1` for public
metadata drift, and `2` when local metadata or public evidence is unavailable.
The underlying exact-name query remains useful for independent inspection:

```bash
curl --get --data-urlencode \
  'search=io.github.anulum/synapse-channel' \
  https://registry.modelcontextprotocol.io/v0.1/servers
```

## Surface audit

The registered MCP tools and resources are checked against this guide by a
source parser:

```bash
.venv/bin/python tools/audit_mcp_surface.py --check
```

The audit fails when `src/synapse_channel/mcp/registration.py` exposes a tool or
resource that this page does not list, or when this page loses the adapter,
authentication, or optional-dependency boundary text. It verifies documentation
completeness for the local adapter surface; it does not certify external MCP
client conformance.

## What stays out of the hub

The adapter holds all MCP knowledge; the hub holds none. The MCP SDK is never a
core dependency, the hub protocol is unchanged, and the bridge translates each
MCP call into an ordinary hub message and correlates the reply. That keeps the
single-transport-dependency guarantee — and the local-first model — intact.

## Outbound: calling external MCP tools

The directions are independent. `synapse mcp` serves the hub *to* MCP clients;
`synapse mcp-tools` and `synapse mcp-call` let a Synapse operator *call* tools on
an external MCP server, with a deny-by-default trust boundary.

An outbound JSON config is executable policy, not ordinary project data: the
server process starts before any MCP tool allowlist can protect you. Synapse
therefore reads it through an owner-only, single-link descriptor, walking every
path component with `O_NOFOLLOW`, and refuses a config inside the active Git
repository by default. Store it under an operator-controlled config directory, for example
`~/.config/synapse/mcp-allow.json`, and run `chmod 600` on it.

Each server needs a raw absolute path with no symlink component. Synapse copies
the validated executable descriptor into a sealed Linux `memfd` and launches
that exact immutable snapshot; a configured cwd is retained through its own
descriptor. `command_sha256` is optional but recommended and is checked against
the bytes that actually execute. `cwd` is required, must be outside the active
repository, and must not be group/world-writable. The repository-local escape
hatch relaxes only repository locality; it never relaxes the mode check.
Low-level library callers that construct a spec without `cwd` are descriptor-bound
to `/`, never to the caller's current directory.
Executable/hash proof covers the configured command, not files named in its
arguments. A shebang script is therefore rejected as `command`: the kernel
would open its interpreter separately, outside the sealed snapshot. Configure
a native interpreter binary as `command`; until auxiliary-artifact pins exist,
`doctor` conservatively warns about the script and every other command argument,
including launcher flags such as `-m`.
The child receives no parent environment values by default. Synapse explicitly
blanks the MCP SDK's baseline POSIX names unless approved; literal `env` entries
are passed exactly, while `inherit_env` approves individual parent variable
names.
An empty tool allowlist denies every tool; `"*"` explicitly opts the whole server
in. A positive finite `timeout_seconds` greater than zero and no greater than
`3600` (default `30`) is the startup and discovery/invocation deadline. Larger
or non-representable values fail schema parsing. Once cancellation begins, the
pinned SDK applies its separate audited two-second graceful process-exit window
before force termination:

```json
{
  "version": 1,
  "servers": [
    {
      "name": "fs",
      "command": "/opt/synapse-mcp/bin/mcp-server-filesystem",
      "command_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "args": ["--root", "/data"],
      "cwd": "/var/empty/synapse-mcp",
      "env": {"MCP_LOG_LEVEL": "warning"},
      "inherit_env": ["LANG"],
      "allowed_tools": ["read_file", "list_directory"]
    }
  ]
}
```

```bash
chmod 600 ~/.config/synapse/mcp-allow.json
synapse doctor --mcp-config ~/.config/synapse/mcp-allow.json
synapse mcp-tools fs --config ~/.config/synapse/mcp-allow.json
synapse mcp-call fs read_file --config ~/.config/synapse/mcp-allow.json \
  --arg path="/data/notes.txt"
```

### Signed outbound manifests

For centrally managed or distributed policy, add a version-1 Ed25519 `signature`
envelope and supply an owner-only trust bundle with
`--config-trust-bundle FILE`. The signature covers the UTF-8 canonical JSON
(sorted keys and compact separators), excluding only the signature `value`.
The signed bytes therefore bind the policy plus envelope version, algorithm,
and whitespace-free `key_id`, after the domain bytes
`SYNAPSE-CHANNEL:MCP-CONFIG:v1\0`. A trust bundle cannot reuse the same public
key under multiple IDs. The envelope is:

```json
{
  "version": 1,
  "algorithm": "ed25519",
  "key_id": "operations-2026",
  "value": "BASE64_ED25519_SIGNATURE"
}
```

The separate trust bundle is also `chmod 600`, outside the repository, and has
this shape:

```json
{
  "version": 1,
  "keys": [
    {
      "key_id": "operations-2026",
      "public_key": "BASE64_RAW_32_BYTE_ED25519_PUBLIC_KEY",
      "revoked": false
    }
  ]
}
```

Passing a trust bundle makes the signature mandatory; a signed config without a
trust bundle also fails closed. `synapse doctor --mcp-config FILE
--mcp-config-trust-bundle TRUST` reports signature, hash-pin, repository, and
environment posture before an operator attempts a call. The explicit
`--allow-repo-mcp-config` compatibility escape hatch keeps the owner-only and
executable checks but accepts a repository-local config or trust bundle and
reports each accepted override as a warning. It never accepts group/world-writable
config, trust, or cwd paths.

Subprocess startup and transport failures cross a stable operational-error
boundary. The synthesized CLI error never reflects raw exception-group text.
Configured server stderr remains attached to the operator's stderr and is not
sanitized, so treat it as trusted server output.

The `mcp` extra installs the audited `mcp==1.28.1` SDK and Ed25519 verification
dependency. Runtime startup also verifies that SDK's inherited-environment list
before spawning, so dependency drift fails closed rather than exposing a newly
inherited name.

This descriptor-bound outbound launcher currently supports Linux with
`memfd_create` and mounted procfs at `/proc/self/fd`. `mcp-tools`, `mcp-call`,
and `doctor --mcp-config` fail closed on macOS, Windows, containers without
procfs, or kernels without sealing support. Those platforms need a future
equivalent native descriptor-execution backend; there is no pathname fallback.
Per-agent ACLs over which identity may invoke outbound MCP remain a later tranche;
the controls here bind the operator's process-launch policy before tool discovery.

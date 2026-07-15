<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# OpenCode bridge

SYNAPSE CHANNEL integrates OpenCode through six deliberately separate surfaces.
Use only the layers needed by one seat; installing the adapter does not silently
start OpenCode, a Synapse hub, or a provider turn.

| Surface | Purpose | Transport |
|---|---|---|
| Project/global adapter | Reversible OpenCode configuration | Filesystem |
| Synapse MCP face | Claims, inbox, board, state, and coordination tools | Local stdio MCP |
| Native mutation guard | Stop covered file tools before execution without a live claim | OpenCode plugin hook |
| Headless participant | Run an exact-version local OpenCode turn | `opencode run --format json` |
| Server participant | Run and cancel an authenticated long-lived server turn | Bounded HTTP(S) API |
| ACP editor face | Use OpenCode from an ACP-compatible IDE | JSON-RPC over stdio |

The verified contract is **OpenCode 1.17.20**, official tag commit
`4473fc3c9055046183990a965d68df3db7ea6f62`. Both participant drivers refuse a
different server or CLI version instead of attempting to parse an unknown
schema.

## Install the bridge

Install the MCP extra and verify that OpenCode is the pinned version:

```bash
python -m pip install 'synapse-channel[mcp]'
opencode --version
# 1.17.20
```

Claim the paths this OpenCode seat may edit, then install the project adapter:

```bash
synapse git-claim OPENCODE-WORK \
  --paths src tests \
  --name my-repo/opencode \
  --auto-release-on manual

synapse adapters opencode install \
  --scope project \
  --project . \
  --identity my-repo/opencode

synapse adapters opencode status --scope project --project .
```

Project scope owns these two paths:

```text
.opencode/opencode.json
.opencode/plugins/synapse-claim-guard.js
```

Global scope uses the OpenCode configuration root instead:

```bash
synapse adapters opencode install \
  --scope global \
  --identity workstation/opencode
```

The default global root is `${XDG_CONFIG_HOME:-$HOME/.config}/opencode`. Use
`--config-root` only when OpenCode itself uses that same override.

### Ownership and reversible changes

The installer owns only:

- the `mcp.synapse` object carrying
  `SYNAPSE_ADAPTER_OWNER=synapse-channel`; and
- the plugin file whose first line is the exact Synapse ownership marker.

It refuses to overwrite an unowned entry or plugin. Configuration reads are
bounded to one MiB. Automatic editing accepts strict JSON only and refuses to
rewrite JSONC. Filesystem mutation rejects final-component symlinks,
non-regular or foreign-owned files, snapshots changed before replacement, and
unsafe modes. Replacement is same-directory, private, fsynced, and atomic.
Existing user keys and the OpenCode `$schema` key are preserved.

Inspect either generated asset without writing it:

```bash
synapse adapters opencode print-config \
  --identity my-repo/opencode \
  --asset config

synapse adapters opencode print-config \
  --identity my-repo/opencode \
  --asset plugin
```

Remove only Synapse-owned assets:

```bash
synapse adapters opencode uninstall --scope project --project .
```

## MCP coordination inside OpenCode

The adapter registers a local MCP server entry equivalent to:

```json
{
  "mcp": {
    "synapse": {
      "type": "local",
      "command": [
        "synapse",
        "mcp",
        "--name",
        "my-repo/opencode",
        "--uri",
        "ws://127.0.0.1:8765"
      ],
      "enabled": true,
      "environment": {
        "SYNAPSE_ADAPTER_OWNER": "synapse-channel"
      },
      "timeout": 30000
    }
  }
}
```

This is a local stdio process even when its Synapse hub is remote. Point the
process at `wss://…` with `--uri`; do not change the OpenCode MCP entry to an
unverified remote MCP transport.

For a secured hub, persist a file path rather than a token value:

```bash
synapse adapters opencode install \
  --identity my-repo/opencode \
  --uri wss://hub.example/ws \
  --token-file /run/secrets/synapse-hub-token
```

`--token` is accepted only for a runtime or dry-run operation and is rejected
for persistent adapter installation. The generated config never contains the
secret value.

After OpenCode starts, its Synapse MCP tools can claim/release work, read the
board and live state, consume the durable inbox, and send handoffs. MCP tool
availability is not wake delivery: keep the exact seat receiver armed as
described in the [MCP wake pattern](mcp.md#wake-and-inbox-pattern).

## Native fail-closed mutation guard

The plugin observes OpenCode's `tool.execute.before` hook and guards exactly:

| OpenCode tool | Path extraction |
|---|---|
| `edit` | `args.filePath` |
| `write` | `args.filePath` |
| `apply_patch` | every add, update, delete, and both sides of a move in `args.patchText` |

For those tools, the plugin sends a bounded JSON event to
`synapse adapters opencode-claim-hook`. It accepts only an explicit
`{"allowed": true}` verdict. A malformed event, invalid Git context, missing
or competing claim, unavailable hub, timeout, non-zero helper exit, excessive
output, invalid UTF-8/JSON, or ambiguous verdict throws before the tool runs.
OpenCode's ordinary permission policy still applies after an allowed verdict.

Do not use `opencode run --auto` to compensate for a guard refusal. `--auto`
changes OpenCode's permission behavior and is intentionally absent from the
Synapse participant driver.

The native hook is not an operating-system sandbox. Shell commands, custom
tools, MCP tools, external programs, and future write-capable OpenCode tools
outside the three names above are not intercepted. Install the staged Git gate
as defense in depth:

```bash
synapse git-init --name my-repo/opencode
synapse git-claim-check --staged --name my-repo/opencode
```

## Participant Fabric

Two providers are registered:

```bash
synapse participant list
synapse participant ask opencode "Review the current claim boundary"
synapse participant ask opencode-api "Review the current claim boundary"
```

`opencode` runs a local `opencode run --format json` process and normalizes its
typed JSONL events. `opencode-api` negotiates `/global/health`, requires version
1.17.20, creates or resumes a session, posts a bounded text prompt, and maps the
source-verified response. Cancellation performs a best-effort
`/session/{id}/abort` request.

The default API endpoint is `http://127.0.0.1:4096`. Configure an authenticated
remote or long-lived server without placing its password on the command line:

```bash
synapse participant ask opencode-api \
  "Review the current claim boundary" \
  --opencode-directory /absolute/project \
  --opencode-endpoint https://opencode.example \
  --opencode-username opencode \
  --opencode-password-file /run/secrets/opencode-server-password
```

The same `--opencode-*` options work on `participant exchange` and
`participant convene`; every OpenCode seat receives the same connection policy.
For the `opencode` provider, `--opencode-endpoint` selects `run --attach`, while
`opencode-api` uses it as the direct API origin. `--opencode-binary` pins an
alternate executable, `--opencode-thinking` includes verified thinking events,
and `--opencode-allow-insecure-http` is the explicit remote-cleartext opt-out.

Applications can construct the participant directly as well:

```python
from synapse_channel.participants import OpenCodeApiParticipant

participant = OpenCodeApiParticipant(
    "my-repo/opencode-api",
    directory="/absolute/project",
    endpoint="https://opencode.example",
    username="opencode",
    password_file="/run/secrets/opencode-server-password",
)
```

The password reader opens one owner-only regular file without following a
symlink, validates the same descriptor, bounds its size, and never places the
secret in the endpoint URL. Literal loopback HTTP is allowed. Remote cleartext
HTTP is refused unless an application explicitly opts into
`allow_insecure_http=True`; remote deployment should use HTTPS.

### Long-lived server and `run --attach`

OpenCode itself can reuse a running server:

```bash
export OPENCODE_SERVER_PASSWORD="$(< /run/secrets/opencode-server-password)"
opencode serve --hostname 127.0.0.1 --port 4096

opencode run \
  --attach http://127.0.0.1:4096 \
  "Inspect the current task"
```

Run the client in a separate shell with the same private environment value, or
use the Synapse participants' `password_file` argument. Do not pass the password
with OpenCode's `--password` flag because command-line arguments may be visible
to other local processes.

Prefer the API participant when Synapse must capture a returned answer.
OpenCode 1.17.20's non-interactive `run --attach` path posts the prompt but
returns before its event subscriber is awaited, so the real CLI can exit zero
with empty stdout even though the server received and executed the prompt. The
focused acceptance test pins that behavior instead of treating an empty stream
as a successful answer.

The Synapse headless participant can be constructed with `attach=…` and an
owner-only `password_file`; it passes Basic-auth values through the child
environment rather than command-line arguments. Its parser still fails closed
when the expected JSONL completion is absent.

## ACP and IDE integration

OpenCode 1.17.20 exposes an ACP subprocess:

```bash
opencode acp --cwd /absolute/project
```

It communicates as JSON-RPC over stdio. Acceptance verifies protocol version 1
and the pinned agent version. The source declares session
load/list/resume/close/fork capabilities, embedded context and image prompts,
HTTP and SSE MCP capabilities, and optional `terminal-auth` metadata. The ACP
process loads the same project configuration, so the installed Synapse MCP entry
and native mutation plugin remain part of the OpenCode runtime.

Official OpenCode 1.17.20 documentation supplies configurations for Zed,
JetBrains IDEs, Avante.nvim, and CodeCompanion.nvim. The common launch contract
is:

```json
{
  "agent_servers": {
    "OpenCode": {
      "command": "opencode",
      "args": ["acp", "--cwd", "/absolute/project"]
    }
  }
}
```

Use the editor's private environment or OpenCode auth store for model-provider
credentials. Do not commit them into an ACP configuration. IDE permission UI is
not a substitute for the native claim plugin; keep both enabled.

## Acceptance and supply-chain gate

The canonical compatibility source is
`integrations/opencode/compatibility.json`.
It binds the official repository, release tag and Git commit, all twelve
published CLI archive digests, the five executable runner lanes, and every
editor/runtime pin used by the real-client workflow. The strict verifier refuses
unknown fields, missing platforms or editor components, changed release URLs or
digests, tag-ref drift, draft/prerelease substitution, and runner-label drift:

```bash
python -m tools.opencode_compatibility_contract --check
```

The `opencode-compatibility` workflow verifies the pinned release against the
official GitHub release and Git-ref APIs on every relevant push or pull request.
Its weekly schedule also compares the pin with the latest stable release. A newer
stable tag is an advisory update signal; it does not silently move the supported
version. Any mutation of the immutable pinned release fails the job.

Five host/architecture lanes download the official archive, verify its manifest
SHA-256, require a bounded single regular root member, and create the binary
through component-by-component no-follow parent traversal without overwriting an
existing path. The downloaded process receives a minimal cross-platform
environment allowlist with isolated home/config/temp roots rather than caller
credentials. Each lane then requires CLI version 1.17.20 and performs a real ACP
initialize exchange:

| Platform artifact | Public runner | Gate |
|---|---|---|
| Linux x64 | `ubuntu-24.04` | Archive integrity + real ACP v1 |
| Linux arm64 | `ubuntu-24.04-arm` | Archive integrity + real ACP v1 |
| macOS arm64 | `macos-15` | Archive integrity + real ACP v1 |
| macOS x64 | `macos-15-intel` | Archive integrity + real ACP v1 |
| Windows x64 | `windows-2025` | Archive integrity + real ACP v1 |

ZIP entry counts are bounded from the end record before Python materialises the
central directory; tar streams have independent member, binary, and expanded-byte
ceilings. Relative binary arguments are made absolute before the isolated ACP
workspace changes directory. The remaining official Darwin x64 baseline, Linux x64 baseline, Linux musl,
Linux baseline-musl, Linux arm64-musl, Windows arm64, and Windows x64 baseline
archives remain digest-bound in the same manifest. They are not labelled as
runtime-tested because GitHub-hosted jobs do not provide each corresponding
runtime environment. Every executable lane must report the pinned `OpenCode`
agent version, ACP protocol 1, HTTP and SSE MCP capabilities, and OpenCode's
terminal-auth command metadata.

The focused `opencode-integration` workflow installs the hash-locked Python
dependency sets, pins OpenCode's official Linux x64 archive and extracted binary
by SHA-256, verifies exact version 1.17.20, builds the wheel, checks its OpenCode
modules, and runs only the OpenCode unit and real-process cohort.

The separate `opencode-editor-e2e` workflow does not substitute a synthetic ACP
peer for an editor. Four fail-closed jobs launch the real client process against
the exact OpenCode binary and a local deterministic provider:

| Lane | Pinned client surface | Required ACP `clientInfo` |
|---|---|---|
| Neovim | Neovim 0.12.4 and CodeCompanion.nvim 19.19.0 | `CodeCompanion.nvim` `1.0.0` |
| Emacs | Emacs 29.3, Agent Shell 0.59.1, `acp.el` 0.12.2, and Shell Maker 0.93.5 | `agent-shell` `0.59.1` |
| Zed | Zed 1.10.3 under Xvfb through `agent::NewExternalAgentThread` | `zed` `1.10.3` |
| JetBrains | IntelliJ IDEA 2026.1.4, AI Assistant 261.26222.65, and required Full Line Code Completion 261.26222.65 under Xvfb | `JetBrains.IntelliJ IDEA` `2026.1.4` |

Version 2 of `integrations/opencode/compatibility.json` is authoritative for
these ACP `clientInfo` tuples as well as the executable and plugin pins.
Release archives are SHA-256 verified; editor plugins are checked out at exact
commits or verified by archive hash and declared version. The JetBrains lane
uses a single exact platform build (`261.26222.65`) for the IDE and both plugin
archives, verifies AI Assistant's required Full Line dependency, completes the
real pinned first-run data-sharing UI, and explicitly declines telemetry. Its
driver requires the exact top-level `Data Sharing` title, fixed dialog geometry,
and X11 root parent before any pointer input, so the nested `Content window`
cannot receive an accidental click. It then binds the late Islands onboarding
popup to its exact geometry and `WM_TRANSIENT_FOR` project owner, selects the
verified `Skip` action, and proves that transient disappeared before sending
ACP shortcuts. IDEA's JVM home is bound to the same isolated profile that owns
the private `acp.json`; the driver waits for exactly one active local ACP agent
before opening the AI Assistant UI. The repository owner records legal consent
through the non-secret `JETBRAINS_USER_AGREEMENT_ACCEPTED_VERSION` repository
variable. The driver accepts only the pinned `2.0` agreement, fails closed on a
missing or different attestation, and still explicitly declines telemetry. A
future agreement revision therefore requires a new affirmative owner decision
and code review. The Zed lane opts into its documented software-rendered CI
mode and completes the real isolated-project trust prompt. Each editor must
identify itself with its exact wire name and version during ACP initialization,
create exactly one session, deliver exactly one acceptance prompt, receive
`end_turn`, and reach the deterministic provider. OpenCode must advertise its
exact command-shaped terminal-auth method.
The proxy records the editor's original capability, maps the standard
`auth.terminal` opt-in to OpenCode's pinned legacy metadata, and injects that
legacy opt-in only when the editor omitted it. An explicit false is never
overridden, and the evidence records whether injection occurred. The same turn
must prove the full governance chain: denied write before ownership, native
OpenCode MCP discovery of `synapse_git_claim`, exact Git/path claim, allowed
claimed write, receipt-bearing release, and denied write after release. The
durable hub journal must retain exactly one matching claim, release, and
assessment receipt.

The JetBrains driver selects only a top-level project frame with the pinned
geometry. Before opening AI Chat, invoking the selector, or entering selector
input, it focuses the validated target and proves that keyboard focus belongs
to that frame or reaches it through a bounded, cycle-free X11 parent chain;
only then does it use the current-focus XTEST path accepted by Swing. The chat
composer applies the same ownership proof after its bounded pointer focus.
The selector re-proves owned focus immediately before final confirmation, after
all filtered-state evidence has been captured. Agent-selector discovery batches
the visible JetBrains window geometry into one X11 query, then performs the
more expensive root-child and transient-owner checks only for windows with the
exact pinned selector dimensions. Malformed batch output, multiple matching
selectors, or a selector whose ownership changes before or after filtering
fails closed. The driver clears the selector filter, types the exact
`SYNAPSE OpenCode E2E` name, revalidates its title and transient owner, captures
the filtered result, and confirms with `Return`; a raw post-confirmation X11
snapshot must prove both the original XID and every valid replacement selector
are absent. A legitimate empty X11 search is accepted only without diagnostics;
timeouts, display/transport failures, malformed geometry, or failed title,
parentage, and transient-owner queries fail closed instead of being interpreted
as disappearance. Ownership tokens must be positive canonical hexadecimal X11
IDs; Python-only signed, underscored, decimal, and octal forms are rejected.
Selector screenshots consume the selection phase's remaining
deadline, are written through an owner-only temporary regular file, and are
sealed to a previously absent destination. Its readiness contract requires the
pinned plugin check before both session start and available-command evidence,
while allowing those independently scheduled completion events in either
observed IDEA 2026.1.4 order. Lifecycle baselines bind each log's device and
inode, reject replacement or truncation, and reject any post-baseline chat or
process event for an agent other than the exact pinned identity. The
implementation keeps orchestration/selection, X11 transport/input, first-run
setup, and evidence capture in separate responsibility modules. The generated
IDEA ACP entry also refuses an empty or relative proxy executable. IDEA starts as an
isolated process-group leader; cleanup terminates every helper with bounded
`SIGTERM`/`SIGKILL` escalation. Its ACP initialization phase has a bounded
three-minute budget for delayed plugin continuations on loaded headless hosts
and a separate five-minute startup budget for first-run, project loading, and
plugin discovery under bounded host contention;
every X11 subprocess receives the lesser of its ten-second ceiling and the
phase's remaining absolute time, including commands inside candidate-window
loops. The prompt phase begins before composer focus and submission. The parent
runner therefore derives a 920-second cap from 750 seconds of bounded phases,
two 15-second standalone evidence attempts (final capture and cleanup fallback),
20 seconds of complete process-group cleanup, and a separate two-minute
supervision margin. Evidence-capture and termination failures are aggregated,
and capture failure cannot skip termination. The outer
editor journey always uninstalls the temporary project adapter and verifies the
original OpenCode configuration was restored, including when the editor or
evidence assertion fails.

Missing clients, changed identity names or versions, changed capabilities,
malformed or replayed traffic, unknown response IDs, absent responses, unsafe
evidence paths, leaked prompt content, a wrong OpenCode/model version, or any
governance mismatch fails the selected job. GUI lanes wait for observable ACP
session state rather than fixed delays. The evidence proxy writes a private
bounded JSONL trace containing protocol metadata and only the prompt's byte
length and SHA-256 digest, never its content. A response must contain exactly
one of `result` or `error`; errors require an integer code and non-empty message,
and malformed responses are rejected before their pending request is consumed.

All four editor lanes are **required** and gate the workflow. Neovim and Emacs
use their headless clients. Zed and JetBrains run their pinned real GUI clients
under Xvfb and target validated X11 windows directly, so they do not depend on a
desktop accessibility bus or window manager. Zed 1.10.3's `--user-data-dir`
contract places the isolated settings and keymap in `<data-dir>/config`; the
driver writes that exact profile with owner-only modes and invokes a dedicated
`ctrl-alt-shift-f12` binding that does not fall through to Zed's built-in
remote-project shortcut. Its X11 driver intersects anchored class and instance
selectors, requires pinned Zed's exact project-root title shape, and binds the
window's `_NET_WM_PID` to the isolated driver process group. Both WM_CLASS
fields must equal pinned stable app ID `dev.zed.Zed`; that identity is part of
the machine-readable compatibility contract. Title-only windows and unrelated
Zed processes cannot receive input. Before prompt input the driver focuses the
owned frame, requires the successful `session/new` response and initial
`session/update`, re-reads the exact raw X11 input-focus XID, and only then types
through a modifier-cleared current-focus XTEST path at a bounded key rate. It
re-proves focus immediately before current-focus submission. Startup, session,
and prompt input each use absolute deadlines; the derived 305-second parent cap
also reserves both screenshot attempts, direct leader cleanup, complete
driver/editor/proxy/helper process-group cleanup, and a separate supervision
margin. JetBrains retries a batched selector snapshot only when every diagnostic
line forms the canonical disappearing-window `BadWindow` plus
`X_GetWindowAttributes` report, and only three times. Mixed, prefixed, duplicate,
or unknown diagnostics are fatal. A persistent race or any other timeout,
transport, warning, malformed output, or ownership ambiguity still fails closed.
A failed Zed or JetBrains real-client turn therefore fails the matrix and the
workflow rather than being hidden by `continue-on-error`.

The real-process suite uses isolated home/config/data/state/cache roots and a
local scripted provider. It proves:

- local JSONL turns and an ACP initialize handshake;
- real CodeCompanion.nvim, Agent Shell, Zed, and JetBrains AI Assistant ACP
  governance turns in their dedicated public-CI lanes;
- Basic-auth refusal, authenticated `run --attach` prompt delivery, direct API
  answer capture, and session behavior;
- adapter install/status/upgrade/uninstall ownership;
- denied-before, claimed-and-allowed, receipt-release, and denied-after native
  writes driven through each real editor and OpenCode's own MCP catalog; and
- preservation of unrelated OpenCode configuration.

These checks validate the pinned connector boundaries. They do not certify an
external model account, unpinned or arbitrary OpenCode/editor builds, the seven
integrity-only archive variants, remote TLS termination, or filesystem
isolation.

## Source references

- [OpenCode 1.17.20 release](https://github.com/anomalyco/opencode/releases/tag/v1.17.20)
- [OpenCode MCP configuration](https://opencode.ai/docs/mcp-servers/)
- [OpenCode ACP support](https://opencode.ai/docs/acp/)
- [OpenCode CLI](https://opencode.ai/docs/cli/)
- [JetBrains ACP agents](https://www.jetbrains.com/help/ai-assistant/acp.html)
- [Zed external agents](https://zed.dev/docs/ai/external-agents)
- [Provider file-edit claim hooks](claim-guard-hooks.md)
- [MCP server face](mcp.md)

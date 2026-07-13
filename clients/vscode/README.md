<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# SYNAPSE CHANNEL — VS Code / Cursor extension

Bring the coordination bus into the editor: claim the file you are about to edit,
release it when you are done, watch the shared board, and see hub health in the
status bar — so two agents (or two people) never edit the same file at once.

This is an installable experimental preview. The structure, editor-agnostic
logic, tests, per-line claim gutter, and repeatable version-pinned `.vsix`
packaging path are in place. It is intentionally kept separate from the core
Python package and is not published in the VS Code Marketplace yet.

## What it does

- **Status bar** — negotiated, live, stale-last-good, incompatible,
  identity-mismatched, or offline hub state, plus live-agent and own-claim counts.
- **`SYNAPSE: Claim current file`** — resolves the nearest canonical Git root
  (or exact workspace root outside Git) and leases one repository-relative path
  under a deterministic per-file task ID. Nested and multi-root workspaces do
  not collapse into an implicit shared-root claim.
- **`SYNAPSE: Release current file`** — releases only the active file's exact
  task; another file claimed by the same identity remains held.
- **`SYNAPSE board` view** — the shared plan's tasks and their status.
- **Claim gutter and overview ruler** — every visible line covered by a file or
  directory claim carries a restrained marker. A circle/check means your claim;
  a diamond/exclamation means another agent's claim, and hover text names the
  owner. Semantic `.synapse-symbol` claims mark only the document-symbol range
  the editor resolves. If no range is available, one explicit alert marker is
  shown instead of falsely widening the claim to the whole file.
- **`SYNAPSE: Set hub token` / `Clear hub token`** — manage one encrypted
  SecretStorage credential per hub URI without writing a bearer to settings.

Configure the hub URI with `synapse.hubUri` (default
`ws://127.0.0.1:8876`) and an optional `synapse.identity`. There is
intentionally no token setting. Run **SYNAPSE: Set hub token** from the Command
Palette; the password input is stored under the canonical hub URI in VS Code
SecretStorage, encrypted by the editor host and not synced across machines.
Changing the URI does not send the old hub's credential to the new endpoint.
Changing `synapse.hubUri` or `synapse.identity` reconnects immediately using the
credential stored for that exact canonical URI.

Plain `ws://` is accepted only for `localhost`, IPv4 `127.0.0.0/8`, or IPv6
`::1`. A non-loopback hub must use `wss://`; the extension refuses remote
plaintext before opening a socket. The TLS certificate must be trusted by the
editor host. Never put a token in the URI, settings JSON, a query parameter, or
workspace files.

## Design

The editor-agnostic fleet decisions — hub health, board items, claim marks, the
claim request for a path, and the status-bar text — live in `src/fleetModel.ts`.
`src/claimGutterModel.ts` separately projects file, directory, whole-worktree,
and semantic claims into visible line and overview-ruler spans after an exact
canonical worktree-and-path match;
`src/claimGutter.ts` is the VS Code-only renderer. `src/hubAuth.ts` owns URI
policy, the registration heartbeat, and the structural SecretStorage adapter;
`src/configurationReconnect.ts` rejects stale, out-of-order credential reads;
`src/hubJson.ts` and `src/hubProtocol.ts` enforce bounded JSON and strict wire
projection; `src/connectionState.ts` owns negotiation and freshness;
`src/hubTransport.ts` owns the reconnecting WebSocket lifecycle while its timer,
close-policy, and public types stay in separate modules. `src/workspaceScope.ts`
owns canonical multi-root scope and per-file task identity.
`src/fleetController.ts` joins validated transport state to editor views.
`src/extension.ts` remains thin activation glue, so no feature turns the entry
point or controller into a Godfile.

The editor advertises wire protocol 2, negotiates down to the hub's supported
version, and permits mutations only in live compatible state. Unknown additive
frames are ignored, while malformed known frames, unsupported protocol versions,
identity-pin mismatches, and stale authority fail closed. Last-good board and
claim data can remain visible while reconnecting to the same hub and identity,
but cannot authorise a claim or release. Switching the hub or identity clears
that projection, and authentication or seat-ownership refusals stop reconnects
until the configuration or credential is changed.

## Develop

```bash
npm ci
npm run typecheck   # strict TypeScript, no emit
npm run coverage    # focused unit + real-hub tests, enforced at >=95%
npm run build       # compile to out/
npm run package:vsix
```

`npm run test:integration` launches two disposable token-gated Python hubs and a
real VS Code Extension Development Host. It proves wrong-token refusal, isolated
per-hub SecretStorage, URI and identity reconnects, authenticated roster
transitions, canonical-root claims, and independent claim/release for two files.
Its multi-root workspace also claims the same relative filename in two Git
worktrees and verifies distinct task and worktree identities.
On headless Linux, run it through `xvfb-run -a`.

The version-pinned VS Code test runtime is cached under
`clients/vscode/.vscode-test-cache`, not a root filesystem temporary cache. Both
that cache and coverage output are ignored and excluded from the VSIX.

`package:vsix` runs the production build through the official VS Code extension
packager, then deterministically normalises entry order and timestamps using
`SOURCE_DATE_EPOCH` (the release epoch is the default). CI packages twice and
requires matching SHA-256 digests before accepting
`dist/synapse-channel-vscode.vsix`. Install that exact local artifact with:

```bash
code --install-extension dist/synapse-channel-vscode.vsix --force
code --list-extensions --show-versions | grep '^anulum.synapse-channel-vscode@'
```

You can also use **Extensions → Views and More Actions… → Install from VSIX…**.
The CI job builds the same package, verifies that its archive contains the
runtime manifest and compiled entry point but no source/test/dependency trees,
and uploads it as the `synapse-channel-vscode-vsix` workflow artifact.

The preview supports an open or token-gated loopback hub and a token-gated
shared hub over trusted `wss://`. The token is connection authentication only;
it does not add identity signatures, per-message authentication, certificate
pinning, or Marketplace publication. The extension remains experimental rather
than part of the supported-core tier.

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

- **Status bar** — hub health (offline / up-no-live-agents / up-with-live-count)
  and how many files you currently hold.
- **`SYNAPSE: Claim current file`** — leases the active file's workspace-relative
  path on the hub.
- **`SYNAPSE: Release current file`** — releases your claim.
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

Plain `ws://` is accepted only for `localhost`, IPv4 `127.0.0.0/8`, or IPv6
`::1`. A non-loopback hub must use `wss://`; the extension refuses remote
plaintext before opening a socket. The TLS certificate must be trusted by the
editor host. Never put a token in the URI, settings JSON, a query parameter, or
workspace files.

## Design

The editor-agnostic fleet decisions — hub health, board items, claim marks, the
claim request for a path, and the status-bar text — live in `src/fleetModel.ts`.
`src/claimGutterModel.ts` separately projects file, directory, whole-worktree,
and semantic claims into visible line and overview-ruler spans;
`src/claimGutter.ts` is the VS Code-only renderer. `src/hubAuth.ts` owns URI
policy, the registration heartbeat, and the structural SecretStorage adapter;
`src/fleetController.ts` owns the WebSocket and hub-state lifecycle.
`src/extension.ts` remains thin activation glue, so no feature turns the entry
point or controller into a Godfile.

## Develop

```bash
npm ci
npm run typecheck   # strict TypeScript, no emit
npm test            # Vitest unit tests for the model and auth policy
npm run build       # compile to out/
npm run package:vsix
```

`npm run test:integration` launches a disposable real token-gated Python hub and
a VS Code Extension Development Host. It proves wrong-token refusal, an actual
SecretStorage store/read/delete cycle, authenticated roster presence, and a real
file claim in the hub state. On headless Linux, run it through `xvfb-run -a`.

`package:vsix` runs the production build through the official VS Code extension
packager and writes `dist/synapse-channel-vscode.vsix`. Install that exact local
artifact with:

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

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
logic, tests, and repeatable version-pinned `.vsix` packaging path are in place;
richer per-line gutter affordances remain open. It is intentionally kept
separate from the core Python package and is not published in the VS Code
Marketplace yet.

## What it does

- **Status bar** — hub health (offline / up-no-live-agents / up-with-live-count)
  and how many files you currently hold.
- **`SYNAPSE: Claim current file`** — leases the active file's workspace-relative
  path on the hub.
- **`SYNAPSE: Release current file`** — releases your claim.
- **`SYNAPSE board` view** — the shared plan's tasks and their status.
- **Overview-ruler marks** — the active file is flagged when it is claimed.

Configure the hub with `synapse.hubUri` (default `ws://127.0.0.1:8876`) and an
optional `synapse.identity`.

## Design

The editor-agnostic decisions — hub health, board items, claim marks, the claim
request for a path, and the status-bar text — live in `src/fleetModel.ts` and are
unit-tested with Vitest, no editor host required. `src/extension.ts` is the thin
VS Code glue that owns the API surface (commands, status bar, tree view,
decorations, the hub WebSocket) and renders what the model computes.

## Develop

```bash
npm ci
npm run typecheck   # strict TypeScript, no emit
npm test            # Vitest unit tests for the fleet model
npm run build       # compile to out/
npm run package:vsix
```

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

The preview currently talks to a local unauthenticated hub over a plain
WebSocket; start one with `synapse hub` (see the repository root) before
launching the extension host. It does not yet expose a token-file setting, so do
not point it at an off-loopback or token-gated hub. Packaging does not change
that runtime/security boundary or promote the extension into the supported-core
tier.

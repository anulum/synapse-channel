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

This is an early stub. The structure, the editor-agnostic logic, and its tests
are in place; packaging to a `.vsix` and the richer gutter affordances are the
next step. It is intentionally kept separate from the core Python package.

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
```

The extension talks to a local hub over a plain WebSocket; start one with
`synapse hub` (see the repository root) before launching the extension host.

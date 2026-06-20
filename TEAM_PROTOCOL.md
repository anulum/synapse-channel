<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Synapse Team Coordination Protocol

Purpose: prevent overlap and accidental regressions when several agents work in
parallel on the same codebase.

## Start sequence

1. Start the hub (defaults to `ws://localhost:8876`):
   ```bash
   synapse hub --port 8876
   ```
2. Join the channel, one session per agent with a **unique** name:
   ```bash
   synapse listen --name USER --uri ws://localhost:8876
   ```
3. Send messages and coordination commands from the command line:
   ```bash
   synapse send --name USER --target FAST "status of TASK-1?"
   ```

To bring up a hub and one or two local model workers together, use
`synapse team`.

## Mandatory rules

1. Claim a task before working on it; if the claim is denied, do not work on it.
2. Keep claim notes short but specific (file or module scope).
3. Update task status as you progress; attach an artefact reference when there is
   a concrete output.
4. Release the task when the work is merged.
5. Use a dedicated lock task (for example `MERGE-LOCK`) around a push, and release
   it immediately afterwards.
6. Names must be unique per live session — the hub rejects a duplicate name with
   `name_conflict` and closes the second session.

## Message types

Agent to hub:

- `chat`
- `claim`
- `release`
- `task_update`
- `resource`
- `state_request`
- `who_request`
- `history_request`
- `heartbeat` (sent automatically by clients)

Hub to agent:

- `welcome`
- `presence_update`
- `claim_granted` / `claim_denied`
- `release_granted` / `release_denied`
- `task_updated`
- `resource_offered`
- `state_snapshot`
- `who_snapshot`
- `history_snapshot`
- `error`
- `name_conflict`

## Recommended task-id pattern

- `H####` for hardening tasks
- `CI-###` for CI workflow repairs
- `DOC-###` for documentation-only tasks
- add a subsystem suffix when useful, for example `H8166-transport`

## Roles

Worker roles are named by capability, not identity:

- `USER` — the human or driving session.
- `FAST` — a low-latency local model worker.
- `REASON` — a stronger local model worker (started only when a distinct
  reasoning model is available).

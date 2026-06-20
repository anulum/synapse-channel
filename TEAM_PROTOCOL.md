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
4. Inspect coordination state at any time:
   ```bash
   synapse board       # the shared task plan, ready tasks, and recent progress
   synapse manifest    # the capability cards agents have advertised
   ```

To bring up a hub and one or two local model workers together, use
`synapse team`. Run `synapse supervisor` to re-offer stalled tasks (LLM-free).
For a hub bound off-loopback, require a shared secret with `synapse hub --token
SECRET` and present it from each agent (`--token SECRET`).

## Mandatory rules

1. Claim a task before working on it; if the claim is denied, do not work on it.
   Declare a file scope (`worktree` + `paths`) so the hub keeps two agents off
   the same files.
2. Keep claim notes short but specific (file or module scope).
3. Update task status as you progress; attach an artefact reference when there is
   a concrete output. Save a `checkpoint` for long work so it can resume if your
   lease lapses.
4. Release the task when the work is merged. To pass unfinished work to a present
   teammate, use an atomic `handoff` rather than release-then-reclaim.
5. Use a dedicated lock task (for example `MERGE-LOCK`) around a push, and release
   it immediately afterwards.
6. Names must be unique per live session — the hub rejects a duplicate name with
   `name_conflict` and closes the second session.
7. The shared blackboard (`ledger_task`) is the plan; advertise what you can do
   (`advertise`) so work can be routed to you by task class.

## Message types

Every message is a JSON envelope (`sender`, `target`, `type`, `payload`,
`timestamp`; hub messages also carry `hub_id`). The full set of `type` values,
grouped by concern:

### Agent → hub

- Presence and chat: `chat`, `heartbeat` (sent automatically by clients).
- Claims and leases: `claim`, `release`, `task_update`, `handoff`, `checkpoint`,
  `wait_request`.
- Resources: `resource`.
- Shared blackboard: `ledger_task`, `ledger_task_update`, `ledger_progress`,
  `board_request`.
- Capabilities: `advertise`, `manifest_request`.
- Queries: `state_request`, `who_request`, `history_request`, `resume_request`.

A state-mutating message may carry an `idem_key` so a retry after a reconnect is
applied once. On a secured hub the first message must carry a `token`.

### Hub → agent

- Session: `welcome`, `presence_update`, `name_conflict`, `auth_denied`, `error`,
  `system` (the generic carrier for hub notices).
- Claims and leases: `claim_granted` / `claim_denied`,
  `release_granted` / `release_denied`, `task_updated`,
  `handoff_granted` / `handoff_denied`, `checkpoint_saved` / `checkpoint_denied`,
  `wait_granted` / `wait_denied`.
- Resources: `resource_offered`.
- Shared blackboard: `ledger_task_posted`, `ledger_task_updated`,
  `ledger_progress_posted`, `board_snapshot`.
- Capabilities: `capability_advertised`, `manifest_snapshot`.
- Queries: `state_snapshot`, `who_snapshot`, `history_snapshot`,
  `resume_snapshot`.

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

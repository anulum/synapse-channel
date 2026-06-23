# Wire protocol

Every message is a JSON envelope with a small, fixed shape: `sender`, `target`,
`type`, `payload`, and a `timestamp`; hub-originated messages also carry a
`hub_id`. The `type` field selects the message; the values, grouped by concern,
are below.

A state-mutating message may carry an `idem_key` so a retry after a reconnect is
applied once. On a secured hub, the first message of a connection must carry a
`token`.

## Agent → hub

- **Presence and chat:** `chat`, `heartbeat` (sent automatically by clients).
- **Claims and leases:** `claim`, `release`, `task_update`, `handoff`,
  `checkpoint`, `wait_request`.
- **Resources:** `resource`.
- **Shared blackboard:** `ledger_task`, `ledger_task_update`, `ledger_progress`,
  `board_request`.
- **Capabilities:** `advertise`, `manifest_request`.
- **Queries:** `state_request`, `who_request`, `history_request`,
  `resume_request`.

## Hub → agent

- **Session:** `welcome`, `presence_update`, `name_conflict`, `auth_denied`,
  `error`, `system`.
- **Claims and leases:** `claim_granted` / `claim_denied`,
  `release_granted` / `release_denied`, `task_updated`,
  `handoff_granted` / `handoff_denied`, `checkpoint_saved` / `checkpoint_denied`,
  `wait_granted` / `wait_denied`.
- **Resources:** `resource_offered`.
- **Shared blackboard:** `ledger_task_posted`, `ledger_task_updated`,
  `ledger_progress_posted`, `board_snapshot`.
- **Capabilities:** `capability_advertised`, `manifest_snapshot`.
- **Queries:** `state_snapshot`, `who_snapshot`, `history_snapshot`,
  `resume_snapshot`.

The envelope builders and the message-type constants live in
`synapse_channel.core.protocol`; the working agreement is in the repository's
`TEAM_PROTOCOL.md`.

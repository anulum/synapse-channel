# Wire protocol

Every message is a JSON envelope with a small, fixed shape: `sender`, `target`,
`type`, `payload`, and a `timestamp`; hub-originated messages also carry a
`hub_id`. The `type` field selects the message; the values, grouped by concern,
are below.

A state-mutating message may carry an `idem_key` so a retry after a reconnect is
applied once. On a secured hub, the first message of a connection must carry a
`token`.

The planned [per-message authentication design](per-message-authentication.md)
keeps the same envelope shape and adds an authentication object for selected
frames after WebSocket connect authentication. It is not implemented yet.

The planned [identity and ACL design](identity-and-acl.md) keeps protocol
messages as ordinary envelopes while adding a future authorization decision
before state mutation or scoped reads. It is not implemented yet and does not
change the current shared-token wire format.

The planned [signed capability cards design](signed-capability-cards.md) keeps
`advertise` and `manifest_request` as ordinary discovery messages while adding a
future card-signature profile for tamper evidence. It is not implemented yet and
does not turn capability cards into authorization or executable trust.

The planned
[differential-privacy blackboard design](differential-privacy-blackboard.md)
keeps `ledger_task`, `ledger_progress`, and `board_request` as ordinary local
messages while defining future redacted or noisy projections for shared reports.
It is not implemented yet and does not anonymize raw event logs.

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

An `advertise` message may include `contracts`, either as a list of contract
objects or as a task-class keyed mapping. The hub normalizes valid entries into
the manifest shape:

- `task_class`: the routing class the contract describes.
- `input_schema` and `output_schema`: JSON-object mappings, usually JSON Schema
  fragments, describing accepted input and produced output.
- `preconditions` and `postconditions`: optional lists of declarative checks.

Malformed contract entries are ignored rather than rejecting the advertisement.
Capability contracts are discovery metadata for routing, dashboards, A2A Agent
Card metadata, and human review; they do not execute checks, authorize callers,
or certify external conformance. The signed capability cards design defines a
future tamper-evidence profile for these advertisements without changing the
current wire format.

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

## Release receipts

A successful `release` may carry closeout evidence. The hub echoes a
machine-readable `receipt` object on `release_granted` with these fields:

- `task_id`, `owner`, and `released`.
- Repeated evidence lists: `evidence`, `artifacts`, `known_failures`,
  `changed_files`, `generated_artifacts`, and `approvals`.
- Optional `confidence` and `freshness_seconds`.

When any evidence field is present, the hub also records a compact
`ledger_progress_posted` assessment note for the same task, so `synapse board`
shows the release closeout alongside the task plan. The hub records the submitted
evidence; policy decisions about whether that evidence is sufficient remain
outside the wire protocol.

## Decoder hardening

Inbound hub and A2A JSON frames use `loads_bounded()` from
`synapse_channel.core.protocol`. The helper scans raw text for array/object
nesting before calling `json.loads`, so a malformed or deeply nested frame fails
as a normal JSON decode error instead of recursing through the interpreter.

`tools/fuzz_protocol_decode.py` is the local decoder hardening evidence harness.
Run `PYTHONPATH=src python tools/fuzz_protocol_decode.py --smoke` for the
deterministic seed corpus, or install Atheris and run
`PYTHONPATH=src python tools/fuzz_protocol_decode.py` for an open-ended fuzzing
session. This is not an external protocol-conformance certification; it is local
coverage for malformed bytes, malformed JSON, quoted bracket runs, valid nested
JSON, and depth-limit rejection.

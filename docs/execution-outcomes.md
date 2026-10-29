<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — scoped execution observations
-->

# Execution outcomes

A transport reply, wake acknowledgement or process exit establishes a result
at that boundary. Work beyond that boundary needs its own verification.
The additive `synapse.execution-outcome.v1` envelope records that distinction.
It is an observation, never an execution grant, lease or independent attestation.

## States and recovery

| `state` | Observation at the named `boundary` |
| --- | --- |
| `not_started` | This invocation did not reach the operation boundary. |
| `started` | The invocation began; completion remains unconfirmed. |
| `confirmed_done` | The named boundary has a positive completion observation. |
| `confirmed_failed` | The named boundary has a confirmed failure observation. |
| `unknown` | Available evidence cannot establish the outcome. |

`operation_id` identifies the observation; `observation` describes its basis.
`external_effect` uses the same five states independently. For example, a
worker exiting successfully confirms its process exit, while its claimed file
write or remote transaction can still have `external_effect: "unknown"`.

`stop_requested`, `stop_confirmed` and `effects_undone` are separate facts.
False means no confirmation is supplied. A local task cancellation does not
confirm that a remote worker stopped. Killing and joining one direct child
does not prove that descendants stopped or that earlier effects were undone.
The built-in adapters never claim an effect was undone.

Every envelope has `automatic_retry: false`. An unknown outcome uses
`recovery: "inquire_or_operator"`; a started operation uses
`"await_observation"`. `external_recovery` separately describes the external
effect. Query current task state, receipts or the worker report before deciding
whether further action is authorised. Reconnection alone never resubmits work.

## Where results appear

| Surface | Report location | Boundary |
| --- | --- | --- |
| A2A task, HTTP/RPC response and task event | `metadata.synapseExecutionOutcome` | `a2a_exchange` |
| MCP mutation tool | `structuredContent.execution_outcome` | `mcp_exchange` |
| Local MCP app-task mutation | Same structured field | `app_task_store` |
| `waker relay` JSON | `execution_outcome` | `wake_transport` |
| Python worker launcher | Returned integer's `.outcome` | `worker_process` |
| Optional worker report and read-only CLI query | Root envelope | `worker_process` |

These Python adapter surfaces do not add a new native hub protocol verb or
automatically execute the operations catalogue. The catalogue remains a
read-only list/describe interface; a listed vendor operation is not a runner.

## Verifying the result

An outcome says that a boundary finished. `synapse verify-result` checks
whether the requested result exists, and records three facts separately in a
`synapse.result-verification.v1` record:

| Field | Values |
| --- | --- |
| `communication` | The boundary state from `--outcome` (an execution-outcome report), or `not_supplied` |
| `effect` | `observed`, `absent` (looked, not there) or `unknown` (could not look) |
| `result` | `verified` only when the observed effect is exactly the requested one; otherwise `not_verified` or `not_checked` |

```bash
synapse worker-session --identity P/worker --outcome-file run.json -- ./make-report
synapse verify-result file --outcome run.json --path report.txt --sha256 <hex> -- pytest tests/test_report.py
synapse verify-result git --operation-id publish-1 --remote origin --branch main --commit <40 hex>
synapse verify-result deployment --operation-id deploy-1 --revision abc123 --report health.json
synapse verify-result migration --operation-id migrate-1 --database app.db --migration 0001_init --schema-sha256 <hex>
```

`file` reads the file without following a final symbolic link and compares
its SHA-256. It runs the given test argv, without a shell, only when the
content matches. `git` reads the remote branch head with `git ls-remote`. The
result is verified when the head is the commit, or when the head is available
locally and contains the commit. When the head is not available locally, the
result is `not_checked`; fetch and verify again. `deployment` checks what a
JSON report says. It is verified only when that report's `revision` is the
requested token and `health` is exactly `ok`. The observation is
`report_names_revision_and_ok`. This is independent evidence only when the
deployed service wrote the report, not when the deploying worker did. The
evidence records `report_sha256` and `report_mtime_ns` of the judged bytes.
`migration` opens a SQLite file read-only and does not follow a final link. The
inode checked with `O_NOFOLLOW` must still be the resolved path after connect.
It requires the migration identity in `schema_migrations.version` (or the named
table and column). A missing column is `migration_column_absent` and
`not_verified`. The schema digest is the SHA-256 of this exact UTF-8 text: for
each `sqlite_master` row with SQL, excluding names that start with `sqlite_`,
ordered by type then name, one block `{type}\t{name}\n{sql}\n`. The SQL is
copied as SQLite stored it, so different whitespace is a different digest.
`synapse verify-result migration --print-schema-sha256 --database ref.db`
prints that digest and exits 0 with no verdict. A WAL database in a directory
the reader cannot write may fail to open, or may miss frames that need a
writable `-shm`; that is `database_not_readable`, not a verified schema. None
of the verifiers fetches, pushes, deploys, migrates, retries or repairs
anything.

A worker that exits 0 and writes the wrong content, the wrong revision, or
the wrong schema is reported as `communication: confirmed_done` with a result
of `not_verified`.

## A2A correlation and retained state

The bridge persists task intent before forwarding. A failed or interrupted
exchange remains available under its original task ID with an unknown outcome.
Recovery of a retained inflight task also leaves its external outcome unknown.
Read the existing task with `GET /tasks/{task_id}`. Retention limits still apply;
an absent task does not establish that work never happened.

Outbound chat metadata contains `a2aTaskId`, `a2aContextId` and an opaque
`a2aOperationId`. A peer should echo all three on its reply. A reply with a
different operation ID cannot finish a newer continuation. A legacy reply
without the operation echo retains compatible task handling, but the execution
observation remains `unknown` with `reply_operation_unconfirmed`.

Within one bridge, continuation preparation and reply, cancellation and timeout
transitions share a lock. A continuation reads the current retained task inside
that lock; an older caller snapshot cannot reopen a terminal or removed task.
Network forwarding runs outside the lock so a reply can arrive before submission
returns. A later forwarding error cannot replace an already terminal transition.
This is local bridge serialisation, not a distributed writer lock.

The operation identity derives from the task, context and request message IDs;
it does not authenticate the business result. A repeated explicit initial
task ID is refused. A2A 1.0 continuation also refuses a message ID already in
that task's retained history. This is not a global request-deduplication service:
losing a response that allocated a server-generated task ID does not make a
new send safe. Preserve task/context/message identifiers and use retained task
inquiries or operator recovery instead of blindly creating another task.

## MCP text, errors and operation identity

Mutation tools retain their human text in the first content block and in
`structuredContent.result`, beside the outcome. Query tools retain their
existing output shapes. Successful socket submission is `started`; a matching
hub reply confirms the exchange, independently of downstream effects. Claim
authorization still requires the exact live lease checks in the claim API.

Stdio invocations allocate a new opaque observation ID. The provisioned HTTPS
profile scopes a caller's `synapse/operation-id` to its native seat and tool,
so a retained keyed operation has a stable observation ID across requests.
This does not broaden the hub's retained idempotency or receiver deduplication
contract. Inspect state before any explicit recovery attempt; the adapter
never initiates a mutation replay. Authentication, SDK validation and deadline
errors can terminate before a tool envelope is available; such an error alone
does not establish whether a native mutation committed.

## Wake receipts

Wake operation IDs bind the canonical marks directory, exact seat and wake
sequence. Existing durable marks control replay. An accepted qualified wake
confirms only `wake_transport`; a cached acceptance is historical evidence,
not proof that the current model read the inbox. An uncertain delivery or
interrupted adapter effect stays unknown and does not trigger another adapter.
An actual refusal can follow the existing refusal-only fallback policy.

## Worker launch and read-only inquiry

Choose a new report path for each launch. The launcher creates an owner-only
file exclusively and refuses an existing report before starting the provider.

```bash
synapse worker-session --identity MY-PROJECT/worker \
  --outcome-file /private/worker-runs/run-001.json -- python -m my_worker
synapse worker-session --read-outcome /private/worker-runs/run-001.json
```

The query requires no provider command or identity and never starts, stops or
retries a process. It refuses malformed or unavailable reports without echoing
their contents. Reports retain no argv, credentials, prompts or returned text.
They include the observation time, return code and the launcher's native birth
reference. Native birth references are read on Linux and Windows. On macOS and
other platforms without a supported birth reader, a pending inquiry returns
`unknown`; a PID alone does not confirm the launcher. If a started report's
launcher can no longer be confirmed, inquiry
returns `unknown` without rewriting the file. Terminal records remain historical
same-account declarations, not evidence of current liveness or independent authority.

Direct launches retain the provider's exit code. Tmux mode observes attachment
exit separately and reports its worker outcome as unknown; attachment return
does not establish the persistent provider's exit. An interrupted direct wait
can confirm only the stop of the child that the launcher killed and joined.

```python
from pathlib import Path
from synapse_channel.worker_session import run_worker_session
from synapse_channel.worker_outcome import read_worker_outcome

result = run_worker_session(
    identity="MY-PROJECT/worker",
    command=["python", "-m", "my_worker"],
    outcome_file=Path("/private/worker-runs/run-002.json"),
)
assert isinstance(result, int)
observation = result.outcome.to_json()
latest = read_worker_outcome(Path("/private/worker-runs/run-002.json"))
```

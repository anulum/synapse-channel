<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — wake relay: order, marks and fallback
-->

# Wake relay

A message waits in the recipient's mailbox on the hub. A **wake** tells the
recipient's session that something is there. It carries no message text and
no authority: the session reads its inbox from the hub.

Relay JSON includes a [scoped execution outcome](execution-outcomes.md) under
`execution_outcome`. Its operation ID binds the marks directory, exact seat and
wake sequence. A cached successful wake is historical transport evidence;
it does not establish current model liveness, inbox reading or task completion.

There is more than one way to wake a session: typing a fixed prompt into an
idle terminal composer, queueing it through a vendor's own daemon, a waiter
whose exit the client reports. Each way is a **wake adapter**. The relay tries
adapters in order and stops at the first one that takes the wake.

!!! note "State of this part"
    `synapse waker relay` calls the relay for one journal message using an
    owner-only native-session binding. The existing supervised pane bridge loop
    remains available; installing a pane waker does not opt it into this command.

## What an adapter may answer

| State | Meaning | What the relay does |
|---|---|---|
| `accepted` | The transport took the wake. A `proves` token says what that shows. | Stops. |
| `refused` | The transport definitely did not take it; nothing happened. | Tries the next adapter. |
| `uncertain` | The adapter cannot tell; the wake may have happened. | Stops. |

`accepted` is not "read". No state says that the recipient saw or acted on
anything. The `proves` tokens are `queued_by_native_daemon`,
`typed_into_idle_composer`, `written_to_passive_receiver` and
`acknowledged_by_passive_receiver`. Another,
`harness_notice_written`, is reserved and said by no adapter today.

An adapter that raises an error, or returns anything other than one of the
three states, is recorded as `uncertain`: the effect may have happened before
the error.

## At most one wake per message

A wake is an effect outside the product. It cannot be taken back, and when its
result is lost it must not be repeated. The relay therefore writes marks on
disk, under `$XDG_DATA_HOME/synapse/wake-marks/<seat>/<wake id>/`:

1. Before an adapter is asked, an **attempt** mark is written and synced.
2. After the adapter answered, an **outcome** mark is written.

The wake id is the hub's sequence number of the message. Attempts are
numbered in order; the number is written with two digits up to 99 and with
more beyond, and marks are read by that number. The seat's and the message's
directories are created with the parent of each new directory synced, before
any adapter is asked. Then:

- A message with an `accepted` or `uncertain` outcome is finished. A second
  delivery of the same message, or a restart, sends nothing.
- A message with an attempt mark and no outcome mark is `uncertain`: the
  process died in between, and the wake may have happened.
- A `refused` adapter is not asked again for that message; the next one is.
- When every adapter refused, the result is `wake_failed`, naming each
  attempt. The message stays in the mailbox in every case.
- An adapter that is not available for the target (session unknown, daemon
  down) is passed over without a mark. Nothing happened, so it may be tried
  when the message is relayed again.

One wake of a seat is at work at a time. A lock per seat is held from the
reading of the marks, through the adapter's call, to the outcome mark; a
second relay for the same seat waits and then finds complete marks. The lock
is an advisory file lock (`flock` on POSIX, a byte-range lock on Windows) and
ends with the process that holds it.

A mark is created by a hard link from a finished temporary file, which never
replaces an existing name: a mark, once written, is not rewritten. The marks
directory must be on a file system with hard links. Marks are kept until the
oldest of a seat are pruned; pruning keeps the newest ones, because a message
whose marks were removed could be woken for again if the hub delivered it
again. Pruning never removes an uncertain wake that no operator has resolved.

## Uncertain wakes reach an end

An uncertain wake is never repeated, so it must not be forgotten either.
`synapse waker uncertain` prints one JSON object for each uncertain wake that
no operator has resolved. It never wakes anyone. Each object gives:

| Field | Meaning |
| --- | --- |
| `seat`, `wake_id`, `adapter` | The wake and the transport that left it uncertain |
| `state` | `uncertain` (the adapter gave no clear answer) or `interrupted` (an attempt with no outcome) |
| `since`, `age_seconds` | When the attempt began or was answered, and its age; `null` when the time cannot be read |
| `repeat` | `safe` for a passive notice (`harness_waiter`); `second_effect` for a transport whose repeat types or queues again |
| `verifier` | No automatic verifier exists; only an operator can inspect the seat's session |
| `operator_actions`, `safe_continuation` | What an operator may do, and when work on this wake continues safely |
| `followed_by` | Later wakes of the same seat that ran while this one was unresolved. Each was a fresh request to the same seat and may have been a second effect |
| `question`, `evidence` | Once the wake is older than `--question-after` seconds (default 900), or cannot be dated, an explicit operator question naming the marks directory as evidence |

The relay itself carries the same question. When `synapse waker relay` meets
an unresolved uncertain wake of the message it was asked to wake, it does not
wake again, and its result names the question in `operator_question` (empty
while the wake is younger than the default 900 seconds, or once it is resolved).

The command exits with status 3 while any operator question is pending, so a
supervisor or scheduler can raise it. An operator answers it with

```bash
synapse waker resolve --seat PROJECT/seat --wake-id 4711 --as read --operator alice
synapse waker resolve --seat PROJECT/seat --wake-id 4711 --as abandoned --operator alice
```

`--as read` records that the seat read the message; `--as abandoned` gives the
wake up. The resolution is written once, as `resolution.json` beside the
marks; a second one is refused. It never runs an adapter again: the message
stays in the mailbox, and the relay still finds the wake finished. Only an
uncertain wake can be resolved.

## The pane bridge adapter

`pane_bridge` wraps the existing pane bridge and changes nothing in it. The
bridge types its own fixed prompt into the provider's composer and reports
success only after it saw the prompt consumed; that is `accepted`, proving
`typed_into_idle_composer`.

The bridge keeps a wake of its own: whenever it is asked and cannot finish
(the pane is busy, a dialog is open, the submit was not acknowledged) it
marks a pending wake and delivers it later by itself. So:

- The adapter asks the bridge only when the session exists, is bound to the
  seat, runs the agent and shows an idle composer. Otherwise it is
  unavailable: the bridge is not touched, nothing is pending, and another
  adapter may run.
- Once the bridge was asked, the answer is `accepted` or `uncertain`, never
  `refused`: the bridge may still deliver the wake it now holds, and a second
  adapter would wake the seat twice.

## The native queue adapter

`native_queue` wakes a Codex session through the client's own queue: it runs
`codex queue --thread <session> --message <fixed prompt>` from a fixed
absolute path, with an argument vector and no shell. The adapter owns what the
command starts, from before the command runs: on POSIX it is started as the
leader of a process group of its own; on Windows it is created suspended, put
into a job object and only then resumed. When that ownership cannot be
established, the command is ended before it ran and the wake is `refused`.
When the time limit passes the whole group or job is ended, also when the
command itself has already exited and only a process it started still holds
the output open; the wait for the remaining output is bounded too. When the command ends in time, nothing is
ended. A daemon that this very call started and that stayed in the group or
job is ended with it; a daemon that was running before is not touched. The session is named by its
identifier alone, and only when that is a UUID in canonical lower-case form;
a session name is never passed. The session must run on this host.

The command takes no key by which a repeat could be recognised, so the
adapter is strict:

| What the command did | State |
|---|---|
| Exit status 0 and `Queued message <id> for thread <session>` for exactly the session asked | `accepted`, proving `queued_by_native_daemon` |
| Was not started, or a non-zero status with a configured refusal text (by default the daemon's answer for a session it does not know) | `refused` |
| No answer within the time limit, a zero status without that line, a non-zero status with any other text | `uncertain` |

`accepted` shows that the daemon queued the message. Whether the session read
it is a separate fact that only the recipient can report.

The adapter's tests start a stand-in program as a real child process; the
vendor's client is not run by them. The command and its two observed answers
come from a manual qualification of 2026-10-04 on one route.

## The harness waiter adapter

Some clients cannot be woken from outside. Their session keeps a waiter
process in the background; the hub writes a directed message to that waiter,
the waiter ends, and the client's own harness tells the session. A relay
cannot start any of that: when the hub delivered the message the wake has
already happened, or it happens when a receiver of the seat later takes the
message from the mailbox, or not at all.

`harness_waiter` therefore does nothing to the seat. It reads the receipt
state the hub's store keeps for the message, and the receipt that set it,
and says what they show:

| Receipt state of the message | Answer |
|---|---|
| delivered at once, actual transport results list a completed `passive` write for the seat | `accepted`, proves `written_to_passive_receiver` |
| delivered at once to a receiver of another declared kind, or not to the seat | `refused`: another adapter may run |
| pending or expired: the message waits in the mailbox | `uncertain`: a receiver that reads the mailbox may still take it; an expired receipt means the hub stopped tracking the receipt, not that the message left the mailbox |
| delivered at once, the kind of the seat's receiver is not declared | `uncertain` |
| older immediate receipt without concrete transport provenance | `uncertain` |
| taken from the mailbox later and acknowledged by a named passive receiver | `accepted`, proves `acknowledged_by_passive_receiver` |
| taken from the mailbox later without a named passive acknowledgement | `uncertain` |
| asked and not yet decided, or no receipt at all, after the time limit | `uncertain` |

It exists so that such a wake is named in marks and rankings like any other,
and so that a second adapter does not wake a seat its waiter woke or may
wake.

`written_to_passive_receiver` says that the hub wrote the message to a
receiver that declared itself passive. It does not say that this receiver was
a waiter a harness watches, that the waiter ended, or that the session was
told. The stronger `harness_notice_written` is reserved: no adapter says it
until a harness managed by the product gives evidence of that.

Deferred receipts include `ack_receiver`, the actual registered connection
that sent the ACK, and `recipient_wake_capabilities`, keyed by logical recipient.
The capability comes from that connection, rather than a different live seat or
sidecar. The existing `acked_by` remains the logical recipient. Wire and journal
receipts carry the same metadata. The ACK proves a recipient acknowledgement;
it does not prove a transport write or a harness notice. Older deferred receipts
without this provenance remain `uncertain`.

Immediate receipts mark `recipient_wake_capabilities_source: transport_results`.
Declarations are captured for the actual named sockets before fanout and retained
only for completed writes. Failed writes remain unknown. A passive sidecar that
completed its write takes precedence over a direct primary connection; an unknown
attempt prevents a definite refusal. Current roster declarations alone cannot
prove what a previous delivery did.

`uncertain` ends the work for a message, and the only definite refusal is a
delivery at once that went elsewhere. Use this adapter only for a seat whose wake path is its waiter, and
only where the hub keeps a receipt for every message the relay may be asked
about; for any other seat it would stop a wake another adapter could make.

## Call the bound relay

Create a single-link owner-only JSON binding file. Use the actual seat, canonical
lower-case native session UUID and `socket.gethostname()` value from the session's
execution host. The binding declares routing; it does not verify that the model
is running or has consumed a message. Every call must supply exactly those three
values, and must run on that execution host. No session-name, latest-session or
remote-host fallback is used.

```json
{
  "schema_version": 1,
  "seat": "PROJECT/codex-main",
  "native_session": "00000000-0000-4000-8000-000000000001",
  "execution_host": "my-workstation",
  "marks_dir": "/absolute/private/marks-for-this-hub",
  "adapters": ["harness_waiter"],
  "journal_db": "/absolute/path/to/hub.db"
}
```

Replace the example values before use. On POSIX use mode `0600`; on Windows
use an owner-only DACL. The loader applies the same secure file policy as waker
configuration, refuses symlinks and multiple links, and reads at most 16 KiB.
Unknown fields, duplicate adapter names, wildcard seats, relative paths and
non-canonical UUIDs are refused. The optional `key_file` names an encrypted-store
key; an unavailable or unverifiable receipt yields `uncertain`.
For a row-authenticated journal, also set `row_mac_key_file` to its existing
owner-only row key. The relay loads that key only; a missing or malformed key
yields `uncertain` without provisioning a replacement or changing the journal.

```bash
synapse waker relay --binding-file /absolute/private/wake-binding.json \
  --identity PROJECT/codex-main \
  --native-session 00000000-0000-4000-8000-000000000001 \
  --execution-host my-workstation --wake-id 123 --deadline 5
```

`--wake-id` is the actual message's journal sequence, not a CLI message id or a
receipt event sequence. Keep one durable marks namespace per hub journal and
retain it across process and native-session restarts. Never reuse a marks
namespace for a different hub or reset journal cursors underneath it. A binding
mismatch, invalid cursor or non-finite/negative deadline fails before any marks
or adapter calls. JSON output reports the state, proof token, attempts,
unavailable transports and whether an adapter was asked in this invocation.
Exit codes are `0` for accepted, `1` for uncertain or wake failed, and `2` for
invalid or unavailable configuration. Re-running the command preserves an
accepted or uncertain outcome; it sends nothing again for that message.

The explicit `adapters` order may also include `native_queue` with an absolute
`codex_bin` path, or `pane_bridge` using the existing armed waker configuration
for exactly the seat. Inhibited or uncertain pane lifecycle control is refused.
The native queue rechecks local-host and fixed-executable availability even on a
direct adapter call. Available ranking samples retain their advisory ordering;
this command does not invent latency samples from receipt lookup time.

## Order of adapters

The harness waiter observes a wake that may already have happened, so the relay
always asks it before active adapters when it is configured. This priority also
applies to direct Python relay calls and overrides latency ranking. Among active
adapters, the configuration gives their order. For
every accepted wake the time from the hub's event to the adapter's acceptance
can be recorded per seat and adapter. Once an adapter has enough samples (five
by default) its median counts:

- adapters with enough samples are sorted among themselves by median, the
  fastest first, and take the places that measured adapters hold in the
  configured order;
- an adapter without enough samples keeps its configured place, so it is
  neither pushed to the end, where it would never be measured, nor to the
  front;
- a tie keeps the configured order.

The newest fifty samples per seat and adapter are kept beside the seat's wake
marks. A samples file that cannot be read counts as no samples.

## What an adapter receives

The exact target (seat, native session identifier, execution host), the wake
id, a time limit, and one fixed prompt built from the seat's identity alone.
The relay has no parameter through which a message's text could reach an
adapter.

## Limits

- The Windows path of the queue adapter (suspended start, job object, resume)
  has not been run by its author.
- The syncs are requested as described; no power-loss test has been made.
- Waker configuration flushes the file before atomic replacement. The shared
  portable directory-sync helper requests directory fsync on POSIX; Windows
  does not provide that directory-sync proof.
- The time limit is per adapter, and must be finite and non-negative. It does
  not bound waiting for the per-seat lock or all configured transports together.
- Receipt reading uses a separate read-only connection beside a running hub;
  it does not start a waiter or tell a harness to watch one. The caller must
  arrange that native waiter lifecycle. No proof token says that a model read
  the message.

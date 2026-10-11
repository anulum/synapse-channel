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

There is more than one way to wake a session: typing a fixed prompt into an
idle terminal composer, queueing it through a vendor's own daemon, a waiter
whose exit the client reports. Each way is a **wake adapter**. The relay tries
adapters in order and stops at the first one that takes the wake.

!!! note "State of this part"
    This page describes the relay's foundation and its first two adapters. No
    service calls the relay yet; the existing pane bridge service works as
    before.

## What an adapter may answer

| State | Meaning | What the relay does |
|---|---|---|
| `accepted` | The transport took the wake. A `proves` token says what that shows. | Stops. |
| `refused` | The transport definitely did not take it; nothing happened. | Tries the next adapter. |
| `uncertain` | The adapter cannot tell; the wake may have happened. | Stops. |

`accepted` is not "read". No state says that the recipient saw or acted on
anything. The `proves` tokens are `queued_by_native_daemon`,
`typed_into_idle_composer` and `harness_notice_written`.

An adapter that raises an error, or returns anything other than one of the
three states, is recorded as `uncertain`: the effect may have happened before
the error.

## At most one wake per message

A wake is an effect outside the product. It cannot be taken back, and when its
result is lost it must not be repeated. The relay therefore writes marks on
disk, under `$XDG_DATA_HOME/synapse/wake-marks/<seat>/<wake id>/`:

1. Before an adapter is asked, an **attempt** mark is written and synced.
2. After the adapter answered, an **outcome** mark is written.

The wake id is the hub's sequence number of the message. Then:

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
again.

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
absolute path, with an argument vector and no shell, in a process group of
its own that is ended when the time limit passes. The session is named by its
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

## Order of adapters

The configuration gives the order in which a seat's adapters are tried. For
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

- The Windows lock has not been run by this project's tests on Windows yet.
- The syncs are requested as described; no power-loss test has been made.
- The time limit is given to the adapter; keeping it is the adapter's duty.

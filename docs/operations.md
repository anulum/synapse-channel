<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — operations catalogue: descriptions and admission
-->

# Operations catalogue

An agent client can be asked to do things beyond exchanging messages: queue a
message into a session, list sessions, read or change a setting. Each such
thing is an **operation**. The catalogue says, per host, vendor, client
version and operating system, which operations exist, what they need and how
well each is known.

!!! note "State of this part"
    This page describes the catalogue's foundation: the description of one
    operation and the catalogue that holds them. No command, no hub verb and
    no adapter uses it yet.

## Being listed grants nothing

The catalogue is data produced on the host where the client runs. A row says
that an operation exists and names the permission its caller needs. Whether a
caller holds that permission is decided elsewhere. The catalogue only decides
whether an operation is known well enough to be tried at all.

## One operation

| Field | Meaning |
|---|---|
| `vendor`, `client_version`, `os`, `host` | Exactly which client this row is about |
| `operation` | Dotted name, for example `session.queue_message` |
| `arguments` | Typed arguments: `string`, `integer`, `boolean`, `uuid`, `enum` (with its choices), `secret_ref` |
| `transport` | `cli`, `rpc`, `acp`, `server` or `pane` |
| `scope` | `session`, `project`, `user` or `host` |
| `required_authority` | The permission a caller needs |
| `side_effects` | What the operation changes, in words |
| `needs_restart` | Whether the client must restart for the effect |
| `interactive` | `no`, `operator_step` or `launch_time_only` |
| `idempotent` | `yes`, `no` or `reconcilable` |
| `qualification`, `evidence` | How well the operation is known, and where that is shown |

A description outside these vocabularies is refused when it is built, and so
is plain data that does not describe an operation.

## Qualification

| Level | Meaning | May run |
|---|---|---|
| `unverified` | Assumed to exist, for example for a client version not seen before | no |
| `help_text` | Read from the client's own help; help text is not a run | no |
| `run_once` | Run for real at least once; evidence named | yes |
| `accepted` | Accepted after the runs its acceptance asks for; evidence named | yes |

A `run_once` or `accepted` row without an evidence reference cannot be built.

## Admission

An operation is admitted only when a row exists for **exactly** the host,
vendor, client version and operating system asked, and that row is `run_once`
or `accepted`. The same operation of another version is another row.

A client version the catalogue does not know admits nothing. Its operations
can be shown as *candidates*: copies of the rows of the newest known version
of that client on that host, marked `unverified`, without evidence. Candidates
are not added to the catalogue.

## Arguments and secrets

Arguments arrive as text and are turned into typed values. An argument the
operation does not have, a missing required one, and a value that does not fit
its type are refused. A session identifier is a UUID in canonical lower-case
form.

A secret is never an argument value. An argument of type `secret_ref` holds a
reference of the form `ref:<name>`, resolved on the host that runs the
operation. So no secret enters a description, a catalogue or a record.

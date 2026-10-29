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

An agent client can be asked to do things beyond exchanging messages: list
sessions, read a setting, queue a message into a session. Each such thing is
an **operation**. The catalogue says, per host, vendor, answering component,
component version, operating system and transport, which operations exist,
how each is invoked, what it needs and how well it is known.

!!! note "State of this part"
    This page describes the catalogue's foundation: the registered names, the
    description of one row with its native binding, the catalogue that holds
    rows, and two commands that read a catalogue file. Nothing runs an
    operation yet: there is no hub verb, no adapter and no permission for it,
    and nothing writes a catalogue file for you.

## Being listed grants nothing

The catalogue is data produced on the host where the client runs. A row says
that an operation exists and names every permission its caller needs. Whether
a caller holds them is decided elsewhere. The catalogue only decides whether
an operation is known well enough to be tried at all. A row qualified on one
host, for one version, says nothing about another host or version.

## Names

An operation is named `<object>.<action>` or `<object>.<aspect>.<action>`, in
this product's words and never in a vendor's: `session.list`,
`plugin.marketplace.list`, `setting.effective.read`. One vendor calls a
conversation a thread and another calls an extension a plugin; the row says
`session` and `plugin`, and keeps the vendor's own name beside it.

The words are registered in `operations/vocabulary.json`, each with its
meaning: objects, aspects (each registered for one object) and actions. A name
with an unregistered word is refused. A word is added by a reviewed change of
that file with a higher `revision`; no name is derived from a vendor's command
by a rule. The same name means the same effect on the same scope for every
vendor; when in doubt, the names differ.

## One row

A row is not a command of a client. It is **one invocation with fixed parts
and a closed set of arguments**. The same command with one more flag can be
another operation: a command that lists can delete with two further flags, and
a command that shows a definition can execute a tool with another. So a caller
never adds words, and an invocation is built from the row alone.

| Field | Meaning |
|---|---|
| `host`, `vendor`, `component`, `component_version`, `os` | Exactly which answering component this row is about. A client's command-line program and its running service are two components and can be different releases |
| `operation`, `transport`, `variant` | The registered name; `cli`, `rpc`, `acp`, `server` or `pane`; and a variant name when one transport offers the operation in two ways. The same operation over two transports is two rows, each qualified on its own |
| `version_printed`, `fingerprint` | The version text as the component prints it, and the SHA-256 fingerprint of the answering executable |
| `binding` | `native_name` (the vendor's own name, kept exactly, never executed); for `cli` the `executable` and the `fixed` words that follow it; otherwise the exact `method` |
| `arguments` | The closed set of arguments, each with its type and its native place |
| `reserved_natives` | Native flags that no argument may ever be bound to, such as a configuration override the vendor accepts on every command |
| `scope` | `session`, `project`, `user` or `host` |
| `effects` | Every effect the operation has |
| `obligations` | For a request transport: each request the component can send back while the operation runs, and how it ends |
| `output_mode`, `output_schema`, `output_projection`, `disclosures` | What comes back and what it can reveal |
| `side_effects`, `needs_restart`, `interactive`, `idempotent` | As words, and from fixed vocabularies |
| `qualification`, `evidence`, `qualified_binding` | How well the row is known, where that is shown, and the digest of the binding that was qualified |

A row outside these vocabularies is refused when it is built, and so is plain
data that does not describe a row, including any field the schema does not
have. Texts are bounded, lists hold at most 64 entries, flags are true or
false.

### Arguments

An argument has a type (`string`, `integer`, `boolean`, `uuid`, `enum` with
its choices, `secret_ref`), may be a list of at most `max_items` values, may
bound an integer with `minimum` and `maximum`, and is placed natively in one
of four ways:

| Placement | Native form |
|---|---|
| `flag` | the native flag, then the value; a list repeats the flag |
| `switch` | a boolean: the native flag when true, nothing when false |
| `position` | a bare word, ordered by `position`; required ones first, a list last |
| `parameter` | a named parameter of a request |

Arguments arrive as text and become typed values. An argument the row does not
have, a missing required one, and a value outside its type, bounds or list
size are refused. An argument that is not given stays absent: that is not the
same as `false` or as an empty text. On a command line, a value that is empty
or begins with a dash is refused, because the component could read it as a
flag. A `pane` row takes no arguments.

For an argument declared `secret_ref`, the parser accepts a reference of the
form `ref:<name>` rather than a literal credential. The catalogue does not
resolve it. A future executor must resolve it on the execution host and keep
the resolved value out of descriptions, catalogue data and records. This grammar
does not detect arbitrary secrets supplied in other text fields; catalogue
authors must keep credentials out of those fields too.

### Effects and permissions

| Effect | Meaning | Permission |
|---|---|---|
| `read` | changes nothing | `operation-read` |
| `change` | changes state that an inverse operation restores | `operation-change` |
| `execute` | starts model work or spends quota | `operation-execute` |
| `extend` | changes what code or tools the client will run | `operation-extend` |
| `approve` | answers an approval, or acts in the user's place towards the client | `operation-approve` |
| `destroy` | cannot be undone | `operation-destroy` |
| `host` | acts on the machine outside a session | none: listed, and never admitted |
| `access` | touches authentication material, or who can reach or control the client | none: listed, and never admitted |

A row lists **every** effect it has, and a caller needs the permission of each
one: no permission implies another. The single effect a listing shows a row
under is for the reader's eye and decides nothing. `extend` and `approve` are
never covered by a seat's own authority; they need a named person's consent,
bound to the request. Starting a turn grants no permission to approve what
that turn asks.

The permission names are fixed by this table. They are not yet permissions of
the hub's access control: until they are, nothing can be granted and nothing
runs.

### Output

`output_mode` is `none`, `text` or `json`. Text is for a person to read; it is
never parsed for a setting's value or for success. `disclosures` names what
the output can reveal whatever the effect: `session_content`, which needs its
own scoped consent, and `credential_bearing`. Raw output that can hold
credentials stays on the execution host: such a row is admitted only when it
names an `output_projection`, a host-side step that removes them.

### What the component asks back

A component reached by requests can ask the caller something while an
operation runs: to approve a command, to answer a question for the user. A row
lists each such request with the way it ends: `refuse` or `error`, in the
protocol's own form; `requires_interaction`, when it is a question for a
person; or `answer_with_consent`, which the row may state only when it has the
effect `approve`. A completion notice or a queued reply is not a result.

## Qualification

| Level | Meaning | May run |
|---|---|---|
| `unverified` | Assumed to exist, for example for a version not seen before, or read from a vendor's documentation | no |
| `help_text` | Read from the installed program's own help or exported schema; that is not a run | no |
| `run_once` | Run for real at least once; evidence named | yes |
| `accepted` | Accepted after the runs its acceptance asks for; evidence named | yes |

A `run_once` or `accepted` row names its evidence, the fingerprint of the
answering executable, and `qualified_binding`: the digest of everything the
qualification vouches for (identity, binding, arguments, reserved names,
effects, obligations, output and scope). When any of these changes afterwards,
the digest no longer matches: the row is **stale** and may not run until it is
qualified again.

### Qualification evidence per profile

The evidence text, fingerprint and binding digest of a row are the operator's
own word. That is enough for a **personal** profile, and the gate says so:
its basis reads `operator_confirmation (personal profile)`.

A **production** profile needs a signed qualification bundle. A qualifier
writes a `synapse-qualification-bundle.v1` body naming:

- the row's `operation`, `transport` and `variant`, and its `binding_digest`;
- the `level` (`run_once` or `accepted`), the `tests`, the
  `configuration_sha256` and the `artefacts` (name → `sha256:` digest);
- the `environment` the qualification ran with (component → identity), for
  example the interpreter, plugins and remote parts;
- the `qualifier`, `issued_at` and `valid_until` (whole UNIX seconds, at most
  366 days apart).

The qualifier signs the body with an Ed25519 key. The signature is
domain-separated and covers the body and the key id:

```bash
synapse ops sign-qualification --bundle-body body.json \
  --key-file qualifier.pem --key-id lab-1 --out session-list.bundle.json
synapse ops gate session.list --profile production \
  --bundle session-list.bundle.json --trust qualifiers.json \
  --require-environment interpreter --require-environment plugin:none
```

The trust file is `{"keys": {"lab-1": "<base64 raw 32-byte public key>"}}`, and
the key file must be owner-only. `synapse ops gate` prints whether the row may
run, on what basis and every reason it may not; it exits 0 or 1 and runs
nothing. In production, a row may run only when all of these hold:

- the row itself is qualified for its present binding;
- a trusted key signed the bundle and the signature verifies;
- the bundle names this operation, transport and variant, and the row's
  present binding digest and qualification level;
- the bundle is valid now;
- `run_once` is used only for a row whose every effect is `read`; any other
  effect needs `accepted`;
- the profile names at least one required environment component, and the
  bundle's environment covers every one. The gate compares component names;
  it does not check that the identities the bundle records match the machine
  or process that will run.

The gate is one precondition of a run. It grants no authority by itself.

## Admission

An operation is admitted only when all of this holds:

- a row exists for **exactly** the host, vendor, component, component version,
  operating system, transport and variant asked;
- it has neither the effect `host` nor `access`;
- it is `run_once` or `accepted`, and not stale;
- the fingerprint of the executable found on the host now is the one the row
  was qualified with;
- if its output can hold credentials, it names a projection.

A version the catalogue does not know admits nothing. Its operations can be
shown as *candidates*: copies of the rows of the newest known version of that
component on that host, marked `unverified`, without evidence, fingerprint or
qualified binding. Candidates are not added to the catalogue.

Admission does not decide whether a person must be present; a row's
`interactive` value says so, for whatever runs the operation. Admission is
also not authority: `synapse ops run` checks the caller's grants itself.

## Running an operation

`synapse ops run` runs one command-line row and nothing else. It is the only
command here that has an effect, and every run takes the same checked path:

1. **Exact target.** The row for exactly this host, vendor, component,
   component version, operating system, transport and variant. The executable
   is resolved on `PATH` and fingerprinted. The row is admitted only when its
   qualification and that fingerprint agree (see *Admission*). A row that
   needs a person's authority or can print credentials is never run.
2. **Qualification gate.** The gate of the named `--profile` must pass:
   `personal` rests on the operator's qualification; `production` needs a
   signed bundle (`--bundle`), the trusted keys (`--trust`) and the
   environment components the profile names (`--require-environment`).
3. **Current authority.** The operator's grants file is read again just
   before the run. It is `{"grants": [{"subject", "permissions", "component",
   "expires_at"}]}`, owned by this user and writable by no one else. Every
   permission the row's effects need must be held, unexpired, by
   `--subject` for this component (or `*`). The gate is checked again too,
   so a bundle that expired since planning refuses the run.
4. **Qualified invocation.** The executable is fingerprinted again. A program
   replaced since planning refuses the run.
5. **Recorded start.** A start record is written and synced before the
   component is invoked, and so is the entry of every directory on its path
   that this user owns, even one another runner has just created.
   An `--operation-id` that has a start is never run again, whatever
   happened to it. A start that cannot be recorded refuses the run.
6. **Effect.** The component runs without a shell, with a deadline
   (`--timeout`, default 60 s). Its standard input and standard error are
   not connected. Its standard output is read as it arrives, without a
   helper thread: the first mebibyte is kept and returned, and every byte read
   is hashed into the outcome record. `output_truncated` says that more was
   read than kept. `output_complete` says the stream reached its end; output
   that a process left behind by the component still holds open is read for
   one more second after the component exits, then the pipe is closed and the
   output is incomplete. Nothing of a run outlives it.
7. **Verification or explicit uncertainty.** The outcome record keeps the
   process result apart from the effect. With `--verify-file` and
   `--verify-sha256` the requested file is checked
   ([result verification](execution-outcomes.md)).

```bash
synapse ops run setting.write --host workstation --vendor acme --component cli \
  --component-version 1.0.0 --os linux --arguments '{"key": "mode", "value": "strict"}' \
  --operation-id set-mode-1 --subject PROJECT/seat --grants ~/.config/synapse/grants.json \
  --profile personal --verify-file ~/.config/acme/config.json --verify-sha256 <sha256>
synapse ops inquire --operation-id set-mode-1
```

A refusal (exit 2) happens before the start record, so a refused run had no
effect, and a malformed `--verify-sha256` is refused there too. After the
start nothing is reported as a refusal: an outcome that cannot be stored is
`unknown` / `outcome_not_recorded`, a verifier that fails leaves the result
unverified with `verification_error`, and output that cannot be read is
named in `output_error` while the component is still waited for and its
outcome recorded (exit 3 for each).

The run's authority is the operator's local grants file, named in
`authority` in the report and in both records. It is not a hub grant and does
not authenticate the seat named by `--subject`.

A plan is a snapshot. `execute` runs the row admitted when the run was
planned and re-checks the grants, the gate and the program; it does not read
the catalogue again, so a row changed or removed on disk after planning is
not noticed. Plan again to pick up a changed catalogue. `synapse ops run`
plans and executes in one call.
The report is one JSON object with `state` (`confirmed_done`,
`confirmed_failed`, `not_started`, `unknown`), `observation`,
`external_effect` (`unknown` unless a verifier observed it),
`verification_error`, `output_truncated`, `output_complete`, `output_error`,
`authority` and
`automatic_retry: false`.

| Exit | Meaning |
|---|---|
| 0 | the component finished and, when a verifier is named, the result is verified |
| 1 | the component failed, the result is not verified, or `inquire` finds no run |
| 2 | refused; nothing was invoked |
| 3 | the outcome or the result is unknown |

**Interrupted and lost runs.** A run killed after its start (the runner
crashed, the machine stopped) has a start without an outcome. A run whose
component outlived its deadline has the outcome `unknown` /
`deadline_passed`. In both cases the component may have had its effect.
`synapse ops inquire` reports such a run as `unknown` and can verify its
effect with the same `--verify-file` options, but it never runs it again.
Running the same `--operation-id` again is refused.

The journal is `$XDG_DATA_HOME/synapse/operations/runs/<operation id>/`
(`--journal` names another). Records are created once, by a hard link from a
synced temporary file, and never replaced.

## Reading a catalogue

```bash
synapse ops list                                   # every listed operation, one per line
synapse ops list --vendor codex --transport rpc --json
synapse ops describe session.list --host workstation
```

Both commands read a catalogue file on this host, by default
`$XDG_DATA_HOME/synapse/operations/catalogue.json` (`--catalogue` names
another), and print what it holds. Nothing is run and nothing is sent.
Files are limited to four mebibytes and 64 levels of JSON nesting. Excessive
nesting is refused before decoding, through the same normal error path as
invalid JSON.

```text
1 operation(s)
workstation kimi cli 2.1.1 linux  session.list  cli/user  read  run_once (may run)
```

A line names the component, the operation (with `#variant` when it is not the
default), transport and scope, the effect it is shown under, and the
qualification. A stale row reads `binding changed since, not run`.
Rows with a forbidden effect or credential-bearing output without a projection
read `not run` even when they were qualified. `may run` means only that the
declared row passes the catalogue's data checks: execution must still check
the current executable, every caller permission and any required consent.

`describe` prints every row of the named operation in full: component, the
version it reports, the native name, how it is invoked, every effect with
every permission, output and disclosures, arguments with their native places,
what the component may ask back, side effects and the qualification with its
evidence. It exits `1` when the operation is not listed. A file that cannot be
read exits `2` with one fixed sentence. A file that is too large, not JSON or
not a catalogue exits `2` with the reason; that includes any value of the
wrong kind inside a row and any name with an unregistered word.

The catalogue file is plain data in the form
`{"schema": "operation-catalogue-v2", "vocabulary_revision": 2, "operations": [...]}`.
A file made with a newer vocabulary than the reader has is refused.

## Files of the earlier schema

A file of `operation-catalogue-v1` is still read, with the same strictness as
before: a field that schema does not have is refused, not dropped. Its rows
are listed and marked `earlier schema, not run`. None of them is ever
admitted, whatever qualification it states, because a v1 row does not say how
the operation is invoked. Nothing converts such a file: a row of the current
schema is made on the host, from the installed component.

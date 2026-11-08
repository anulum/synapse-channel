<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Integrator contract

An integrator connects its own executor, an agent runner, a CI job or a
deployment tool, to SYNAPSE CHANNEL. It needs six things, and this page names
the one command or call for each. Everything here already exists; the page says
which of it is promised. `tests/test_integrator_contract.py` pins every command
option, Python name and schema identifier used below, so an internal change
that leaves them alone cannot break an integrator, and a change to one of them
is a reviewed edit of this page and that test.

A runnable adapter does all six steps against its own in-process hub:

```bash
python examples/integrator_adapter.py
```

It prints `success: integrator adapter completed`.

## 1. Register an executor

Give the executor one identity, `<project>/<name>`, and connect with it. A
long-lived seat keeps a waiter armed (`synapse arm`, or `synapse arm install
--identity NAME --start` for a permanent user service); a one-shot executor
connects when it acts. In Python: `SynapseAgent(name, callback, uri=...)`.

## 2. Propose an operation

Before its effect, the executor declares the operation on the task it holds:

```bash
synapse operation declare --task T --name P/worker --epoch E --effect file \
  --path out/report.txt --sha256 <hex>
```

`--operation-id` may be omitted inside `synapse worker-session`, which sets
`SYN_OPERATION_ID`. Effects are `file`, `git_publication`, `deployment` and
`migration`, each with its own target options. Only the live claim holder at the
current epoch may declare, and an operation id is declared once.

## 3. Obtain authority

Authority is a claim on the task: `SynapseAgent.claim(task_id, paths=[...])`
returns a `claim_granted` frame with the `epoch` that fences every later record,
or `synapse git-claim` for the current branch's paths. A denied claim is a stop.
ACL rules, role grants and approvals apply where the hub configures them.
`SynapseAgent.release(task_id, epoch=E)` ends the claim.

## 4. Execute

```bash
synapse worker-session --identity P/worker --outcome-file run-001.json -- ./do-the-work
```

The provider runs with `SYN_OPERATION_ID`; the launcher writes an owner-only
`synapse.execution-outcome.v1` report that keeps the process exit apart from the
external effect. In Python: `run_worker_session(identity=..., command=[...],
outcome_file=...)`.

## 5. Follow the result

The outcome says that a boundary finished; it is not proof of the effect.

```bash
synapse verify-result file --path out/report.txt --sha256 <hex> --outcome run-001.json > verification.json
synapse operation outcome --task T --name C/checker run-001.json
synapse operation verification --task T --name C/checker verification.json
```

`verify-result` writes a `synapse.result-verification.v1` record: `verified` only
when the observed effect is exactly the requested one. Outcome and verification
records are published by an identity the ACL policy grants `operation-evidence`
on the task, usually a checker separate from the worker; an open hub keeps the
declaration but refuses unauthorised evidence. In Python: `read_outcome`,
`verify_file_change`, and an `operation_record` frame.

## 6. Recover after an unknown outcome

```bash
synapse worker-session --read-outcome run-001.json
```

Reading the report never starts, stops or retries anything. Every outcome says
`automatic_retry: false`; `unknown` means inquire (read the report, the task
state and the published records) or ask an operator. In Python:
`read_worker_outcome(path)`. Across hubs, the fleet tier's incident timeline
turns the same records into `SAFE_TO_RETRY`, `ALREADY_HAPPENED` or
`UNKNOWN_INQUIRE` per task.

## Further reading

[Execution outcomes](execution-outcomes.md) · [Git-native claims](git-claims.md)
· [Identity and ACL](identity-and-acl.md) · [API and wire stability](api-stability.md)

# Policy engine

The policy engine is the decision layer that reads existing Synapse evidence and
answers one narrow question: is a task or branch ready to proceed under the
operator's declared rules?

It is advisory by default. It reports pass, warn, or fail decisions with exact
evidence references, but it does not merge code, does not replace code review,
and does not call external policy services. A future enforcement mode can wire
the same decisions into git hooks or CI after operators have reviewed the rule
set on their own repositories.

## Implemented (first tranche)

The advisory decision layer is implemented in
:mod:`synapse_channel.core.policy_engine` and
:mod:`synapse_channel.core.policy_rules`:

- `synapse policy-check TASK --policy <file> --receipt-json <file> [--json]`
  evaluates a release receipt against a small JSON (or TOML on Python 3.11+ /
  with `tomli`) policy and prints deterministic pass / warn / fail /
  not_applicable decisions, each with the evidence it used and a next action.
- The rule families evaluated from receipt evidence are required tests, strict
  type checking, owner approval, evidence freshness, no-merge-without-receipt,
  known-failure acknowledgement, and generated artifact parity.
- It is advisory by default and exits `0`; `--enforce` exits non-zero only when
  an enforcement-mode policy has a failing rule.

CODEOWNERS-sourced approval mapping, event-store-backed claim coverage, and git-
hook enforcement remain design targets described below.

## CI gating with the GitHub Action

The repository root ships a composite GitHub Action (`action.yml`) wrapping the
same command, so a repository can gate its pipeline on a receipt without
writing the install-and-invoke boilerplate:

```yaml
- name: Check the release receipt against policy
  uses: anulum/synapse-channel@v0.89.0
  with:
    task: release-1.4
    policy: .synapse/policy.json
    receipt-json: artifacts/receipt.json
    # optional hardening: recompute and require a trusted hub signature
    merkle-db: artifacts/hub.db
    trusted-signing-keys: |
      .synapse/hub-a.pub
      .synapse/hub-b.pub
    version: "0.89.0"   # pin the checker itself for reproducible gating
```

The action installs `synapse-channel` (pin it with `version:`), runs
`synapse policy-check --json --enforce` with the given inputs, prints the
decision report, exposes it as the `report` step output, and fails the job
exactly when the CLI exits non-zero. Set `enforce: "false"` for an advisory,
report-only run that never fails the job. Inputs reach the shell through
environment variables — never interpolated into the script — so untrusted
values cannot inject commands; paths with spaces survive intact.

## The SYNAPSE-protected badge

A repository whose pipeline gates on the action may say so with a badge:

[![SYNAPSE protected](https://img.shields.io/badge/SYNAPSE-protected-6b46c1)](https://anulum.github.io/synapse-channel/policy-engine/)

```markdown
[![SYNAPSE protected](https://img.shields.io/badge/SYNAPSE-protected-6b46c1)](https://anulum.github.io/synapse-channel/policy-engine/)
```

```html
<a href="https://anulum.github.io/synapse-channel/policy-engine/"><img src="https://img.shields.io/badge/SYNAPSE-protected-6b46c1" alt="SYNAPSE protected"></a>
```

**What the badge claims.** Adding it declares that the repository's CI runs
the `anulum/synapse-channel` action (or `synapse policy-check --enforce`
directly) as a **gating** step — a failing enforcement-mode rule fails the
pipeline — on the workflows that release or merge its code. It claims
nothing else: not that the policy is strict, not that the coordination it
gates was correct, and not that any third party audited it.

**Wear it only when it is true.** The badge is eligible when all three hold:

1. a workflow runs `uses: anulum/synapse-channel@<tag>` (or invokes
   `synapse policy-check` itself) with enforcement on — the action's
   default; `enforce: "false"` is an advisory run and does not qualify;
2. that workflow gates the protected path — releases, or PRs into the
   default branch — rather than running somewhere its failure cannot block;
3. the policy file it checks is committed to the repository, so readers can
   see what is actually enforced.

**How a reader verifies it.** The badge is a static image and a
self-declaration — there is no attestation service behind it. Verification
is one look at the repository itself: open `.github/workflows/`, find the
workflow with `uses: anulum/synapse-channel@`, and confirm it runs on the
protected path with enforcement on and a committed policy file. A badge on
a repository where that search comes up empty is a false claim, and the
linked page — this one — tells every reader exactly how to check. A
managed GitHub App that issues the badge from observed check runs (turning
self-declaration into attestation) is a separate layer under design in
[the managed GitHub App design](managed-github-app.md); the badge
deliberately ships first because it needs no hosting at all.

## Goals

- Keep coordination local-first and evidence-bound.
- Make release decisions repeatable across agents, terminals, and CI.
- Explain every decision with file paths, event sequences, command outputs, or
  receipt fields.
- Let teams add stricter rules without changing the hub protocol.
- Preserve simple local use: if no policy file is configured, current commands
  keep their present behavior.

## Non-goals

- It is not a remote compliance service.
- It is not an automatic merge bot.
- It is not a replacement for human owner approval where a repository requires
  that approval.
- It is not a scorer for agent reputation or hidden trust ranking.
- It is not a sandbox, encryption layer, or signed-event system by itself.

## Inputs

The policy engine should consume existing Synapse and repository evidence before
adding new protocol fields:

- `synapse release` release receipt payloads: changed files, generated
  artifacts, evidence entries, known failures, approvals, confidence, freshness,
  and epistemic status.
- `synapse verify-release` observed receipt payloads: declared command argv,
  exit codes, stdout/stderr SHA-256 digests, artifact hashes, Git `HEAD` and
  tree ids, changed files, timestamp, and optional signature reference.
- `synapse event-query` snapshots for task timelines, task state at a sequence,
  path-touch windows, and reconstructed conflicts.
- `synapse postmortem` reports for replayable task context when a release needs
  incident-style review.
- `synapse reliability` evidence for stale claims, declared failed checks,
  broken handoff candidates, and conflict history.
- The [agent trust graph](agent-trust-graph.md) (`synapse trust-graph`), which
  exposes reliability signals, positive release receipts, broken handoff
  candidates, and conflict history as policy input without ranking agents.
- The read-only `synapse git-claim-check --staged` gate when a repository wires
  it into pre-commit. `synapse git-init` persists non-secret local connection
  metadata but installs only non-blocking post-commit/post-merge auto-release
  hooks; it does not install a universal commit, merge, or push policy gate.
- Repository-local files such as `CODEOWNERS`, generated-artifact maps, test
  ownership maps, and optional policy configuration.

## Initial rule families

The first policy schema should cover these enterprise rules without requiring a
cloud service:

- **Required tests**: declared test commands must appear in release evidence,
  and failed required commands must either be absent or listed as a
  known-failure acknowledgement with an owner-visible reason.
- **Strict type checking**: configured typecheck commands, such as strict mypy
  on touched Python files, must appear in evidence for code changes.
- **Owner approval**: paths mapped by `CODEOWNERS` or a policy owner map can
  require an approval entry before a hard gate passes.
- **Evidence freshness**: evidence can expire by age, by event sequence, or by
  base revision drift so old green checks cannot be reused after meaningful
  changes.
- **No-merge-without-receipt**: merges can require a release receipt that names
  changed files, generated artifacts, known failures, and verification evidence.
  A `supported` receipt status means current submitted evidence exists; it does
  not independently certify correctness, command choice, or artifact sufficiency.
- **Claim coverage**: changed files should be covered by an active or recently
  released file-scope claim for the same task.
- **Generated artifact parity**: configured generated outputs must be updated or
  explicitly justified when their source files change.
- **Known-failure acknowledgement**: a release with known failures must name the
  failure, scope, reason, owner, and follow-up path.

## Decision model

A policy decision should be deterministic and serializable:

```json
{
  "status": "warn",
  "rule": "evidence_freshness",
  "subject": "TASK-123",
  "reason": "mypy evidence is older than the latest changed source event",
  "evidence": ["event:1842", "receipt:TASK-123"],
  "next_action": "rerun strict type checking and attach the result"
}
```

Statuses:

- `pass`: the rule is satisfied by current evidence.
- `warn`: the rule found a gap that should be reviewed but is not a configured
  hard stop.
- `fail`: the rule is configured as required and current evidence is
  insufficient.
- `not_applicable`: the rule does not apply to the requested subject.

## Configuration shape

The first configuration file can be static YAML or TOML and should remain small:

```yaml
version: 1
mode: advisory
rules:
  required_tests:
    commands:
      - ".venv/bin/python -m pytest tests/test_policy_engine_design_docs.py -q"
  strict_type_checking:
    python:
      command: ".venv/bin/python -m mypy --strict {files}"
  owner_approval:
    source: CODEOWNERS
  evidence_freshness:
    max_age_seconds: 3600
  no_merge_without_receipt:
    required: true
  generated_artifact_parity:
    map: "tools/generated_dependency_claims.py --json"
```

The command should validate configuration before use and print a stable JSON
decision report. Invalid policy should fail closed for enforcement mode and fail
open with warnings for advisory mode.

## Rollout path

1. Add a read-only `synapse policy-check` command that accepts a task id, branch
   diff, event store, and optional policy file.
2. Emit human and JSON reports using the decision model above.
3. Teach `synapse release` to attach policy-check output as ordinary evidence,
   without changing receipt semantics.
4. Add git hooks integration as opt-in advisory output.
5. Add future enforcement mode only after teams have validated the advisory
   output on real repositories.

## Security and trust boundaries

The policy engine should only trust local evidence sources selected by the
operator. It must not fetch remote policy at decision time, must not execute
untrusted generated commands from the event log, and must quote command evidence
as observed text rather than treating it as proof. For exposed deployments,
policy enforcement depends on later identity, signed events, and ACL work; this
design deliberately does not claim those guarantees.

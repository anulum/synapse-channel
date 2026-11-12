<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Effect-safety suite

The effect-safety suite asks one question of a coordinated system: is its
effect controlled? It does not test whether an agent is capable. It tests
whether the system around the agent refuses what is not approved, performs what
is approved at most once, and leaves a record that someone else can check. It
runs the system against a real, throwaway Git repository and a real hub, and it
reads the result itself: a verdict never comes from the system's own report.

Version 0.1 has cases 1 to 4 in twenty-one variants. This package ships it with
one system under test, its own stack, driven through the six steps of the
[integrator contract](integrator-contract.md).

## Run it

`git` must be on `PATH`, and signing and verifying need the `cryptography`
package of the encryption extra (`pip install 'synapse-channel[encryption]'`);
without either, the command refuses with words and runs nothing. Records are
signed with an Ed25519 private key file, the format `synapse merkle keygen`
writes:

```bash
synapse merkle keygen suite.key
synapse effect-suite run --signing-key suite.key --output results/
synapse effect-suite verify --public-key suite.key.pub results/*.json
```

The key file must be readable by its owner only and have no second link; the
command never prints it. Each record names the key's id and says how the key is
held: by the operator, while the group's decision on key custody is still open.

`run` prints one line per variant, the failed and unknown ones first, then how
many records it wrote and the signing key's id. It prints no score.
`--variant NAME` runs only the named variants and can be repeated. Each variant
works in its own temporary folder under `--output`, removed when the variant
ends; the records, `effect-suite-<variant>.json`, stay. `verify` needs no hub
and prints `valid PATH` or `invalid PATH: reason` for each record.

| Exit | `run` | `verify` |
| --- | --- | --- |
| 0 | every variant passed or is not applicable | every record verifies |
| 1 | a variant failed or is unknown | a record does not verify |
| 2 | no `git`, no `cryptography`, an unusable key, or an unknown variant name | no `cryptography`, an unusable public key, or a record that cannot be read |

## What one variant does

Each variant starts a fresh repository. Its branch `main` holds the base B,
whose parent is A. Three commits are prepared, and no ref points at them: the
candidate X and another candidate X′, each adding `report.txt` with mode
`100644` to B, and O, a sibling that adds another file. The repository logs
every ref update (`core.logAllRefUpdates=always`).

A hub is served on a free local port with its journal in the same folder. Five
identities connect: an executor, a reviewer and an approver of one project; the
suite's observer, of another project; and a stranger.

The executor follows the integrator contract. It claims the task and declares
the publication of X with an authorisation intent. The reviewer records an
accepted verdict on the intent's digest, and the approver approves it. The
executor then asks the hub to open an attempt. Only when the answer is
`approved_fresh` and the attempt is open does it move the branch, and only by a
compare-and-swap from exactly B to X (`git update-ref refs/heads/main X B`),
never by a fast-forward. Then it publishes its own outcome, which ends its
attempt. Each variant changes one thing in this sequence.

## What the observer reads

The observer is the suite's own. With its own `git` calls it reads:

- the commit the branch holds, named by its role: `a`, `b`, `x`, `x_other`,
  `o`, or `other`;
- how many times the reflog shows the branch set to a candidate; a write that
  was later undone still counts;
- whether X is published: the branch holds X, X's parent is B, and the change
  from B to X is exactly one added file, `report.txt`, with mode `100644`;
- the branch's commit and tree before the variant acts and after it ends.

It publishes its verification of the operation under its own identity, which
the hub accepts as an observation by someone other than the executor. After the
hub stops, it reads the journal: every answer to an attempt request for the
variant's task, the attempts opened (the answers `approved_fresh`), and the
approvals that the verdict gate refused.

A variant **passes** when every reading matches the table below. It **fails**,
naming each reading that differs, otherwise. It is **unknown** when a reading
could not be made: a `git` command failed, or the hub did not answer within 30
seconds. It is **not applicable** when this profile cannot run it, with the
reason.

## The variants

Answers are the hub's, in order; a declaration's answer is written
`declare:<answer>`. *Branch* is the role of the commit the branch holds at the
end; *published* counts publications from the reflog; *verified* is the
observer's reading of X.

| Variant | What changes | Answers | Branch | Attempts | Published | Verified |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | nothing: one approved intent is published once | `declare:accepted`, `approved_fresh` | x | 1 | 1 | yes |
| 2a | no approval | `declare:accepted`, `not_approved` | b | 0 | 0 | no |
| 2b | no verdict, so the gate refuses the approval itself (one refusal journalled) | `declare:accepted`, `not_approved` | b | 0 | 0 | no |
| 2c | the approval came through a switched-off gate | `declare:accepted`, `unknown` | b | 0 | 0 | no |
| 2d | a dry run that stops after preparing | `declare:accepted` | b | 0 | 0 | no |
| 2e | a request from an identity without the claim, then one with a stale epoch | `declare:accepted`, `not_claim_holder`, `stale_epoch` | b | 0 | 0 | no |
| 3a | another candidate, X′ | `declare:accepted`, `not_approved` | b | 0 | 0 | no |
| 3b | another branch | `declare:accepted`, `not_approved` | b | 0 | 0 | no |
| 3c | another tool version | `declare:accepted`, `not_approved` | b | 0 | 0 | no |
| 3d | the base moved to a sibling before the publication | `declare:accepted`, `approved_fresh` | o | 1 | 0 | no |
| 3e | the branch was rewound to an ancestor of the base | `declare:accepted`, `approved_fresh` | a | 1 | 0 | no |
| 3f | a revision declared after the executor read the first | not applicable: intents of this version carry no revision | | | | |
| 4a | the same declaration twice | `declare:accepted`, `declare:duplicate_operation`, `approved_fresh` | x | 1 | 1 | yes |
| 4b | the executor starts again after its verified publication | `declare:accepted`, `approved_fresh`, `operation_closed` | x | 1 | 1 | yes |
| 4c | two requests at once | `declare:accepted`, `approved_fresh`, `attempt_exists` | x | 1 | 1 | yes |
| 4d | the approval of one operation used for another | `declare:accepted`, `declare:accepted`, `not_approved` | b | 0 | 0 | no |
| 4e | a request repeated before its answer was read | `declare:accepted`, `approved_fresh`, `approved_fresh` (the stored answer) | x | 1 | 1 | yes |
| 4f | open, decline, observe, then ask again on the same approval | `declare:accepted`, `approved_fresh`, `attempt_exists` | b | 1 | 0 | no |
| 4g | a second approval while an attempt is open | `declare:accepted`, `approved_fresh`, `attempt_exists` | x | 1 | 1 | yes |
| 4h | publish, crash, restart and repeat the request | `declare:accepted`, `approved_fresh`, `approved_fresh` (the stored answer) | x | 1 | 1 | yes |
| 4i | an ended attempt not yet observed, and a new approval | `declare:accepted`, `approved_fresh`, `earlier_attempt_unreconciled` | b | 1 | 0 | no |

In 3d and 3e the attempt opens, the compare-and-swap is refused because the
branch no longer holds B, and nothing is applied. In 4h the second
compare-and-swap is refused because the branch already holds X, so the restart
publishes nothing twice.

## Results

The first kept run, on 2026-11-12, of the package at version 0.99.51 with this
suite (Linux, Python 3.12.3, Git 2.43.0): 20 variants pass, 3f is not
applicable, and all 21 records verify offline with the run's public key. No
variant failed and none was unknown.

## The record

Each variant gives one `synapse.effect-suite-result.v0` record, written as JSON:

- the suite version, case, variant and title;
- the profile claimed and the profile computed, both `cooperative`, and the
  sentence `cooperative: bypass not excluded`;
- the system under test: package name and version, a SHA-256 over the
  package's Python sources, and a SHA-256 of the adapter's source;
- what the case table expects and what the observer read, including the
  branch's commit and tree before and after, the journal's answers and the last
  journal sequence it read;
- the verdict and its reason;
- the signature: Ed25519 by the key named by its id, with the custody sentence.

The signature covers the record's canonical bytes without the signature
(RFC 8785, under the integer-only profile of the evidence format), after the
fixed prefix `synapse.effect-suite-result.v0` and a newline. The prefix keeps a
record signature from passing as any other signature of the same key.

## Limits

- **Cooperative profile.** The executor follows the protocol, and nothing stops
  an executor that does not. A variant that needs an executor confined to the
  hub's path would be reported as not applicable, never as a pass.
- **One system under test.** Version 0.1 drives this package's own stack. The
  variants also use the reference adapter's lower-level calls to stage races,
  crashes and requests from other identities, so another framework's adapter is
  a later step.
- **One effect.** The reference effect is a compare-and-swap of a branch in a
  local repository. No hosted service is involved.
- **Cases 1 to 4 only.** Crashes before the effect with a lost answer, revoked
  grants, forged records and the rest come later.
- **No tree head over the records.** Each record is signed on its own.
- **Variant 3f** needs intents that carry a revision.

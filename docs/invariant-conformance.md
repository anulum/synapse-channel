<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Invariant conformance

SYNAPSE CHANNEL maintains a machine-checked conformance registry for six
coordination boundaries. It is a proof map, not a certification badge: each row
names its exact normative invariants, implementation files, executable evidence,
scope, and residual limitations.

The current programme status is **partial**. Two bounded guarantees conform and
four remain partial. The generated
[`invariant_conformance.json`](https://github.com/anulum/synapse-channel/blob/main/docs/_generated/invariant_conformance.json)
is suitable for automation; `tools/invariant_conformance.toml` is its canonical
source.

| Boundary | Status | Current truth |
| --- | --- | --- |
| Single authority | Conformant | One hub authority domain prevents overlapping ownership; federation does not merge authority. |
| Immediate-effect fencing | Partial | Hub-mediated epochs and versions fence stale mutations, but direct external effects are outside that boundary. |
| Atomic operation truth | Partial | Claim-family operations commit before publication and apply once; this is not yet universal. |
| Content-bound global event identity | Conformant | A federated identity binds one fingerprint; equivocation quarantines before publication. |
| Causal conflict handling | Partial | Equivocation fails closed, but the board fold is explicitly non-causal and general conflict objects do not exist. |
| Evidence completeness | Partial | Defined journal, receipt, AEF, and quarantine evidence exists, but coverage is not universal. |

`python tools/invariant_conformance.py --check` validates the schema, exact six-row
set, cited invariant identifiers, public evidence paths, and generated output.
This freshness check runs before commit and in reserved release preflight.
`--enforce` is intentionally red until every row is truthfully conformant; it
prints every incomplete boundary and exits non-zero.

Ordinary line or branch coverage cannot close a boundary by itself. The registry
links state-machine exploration, kill-point fault injection, multi-process races,
restart and concurrency probes, adversarial inputs, and receipt/replay tests where
those modes apply. A status changes only when both the normative guarantee and
its hostile executable evidence change together.

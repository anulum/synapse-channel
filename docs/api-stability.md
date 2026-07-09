<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# API and wire stability

SYNAPSE CHANNEL is in its pre-1.0 (`0.x`) line. This page states what counts as a
stable surface, how each surface is guarded against accidental change, and what
`1.0.0` will lock. It is the contract an integrator can rely on when pinning a
version — in particular an out-of-tree consumer that builds on the core, such as
the commercial fleet tier, which speaks the wire across hubs and imports the
federation primitives directly.

## Versioning

Releases follow semantic versioning. In the `0.x` line a minor release may change
a public surface, but only as a reviewed edit to the pin that guards it (below),
never silently. `1.0.0` locks the public Python API and the wire; after it, a
breaking wire change bumps the wire-protocol version and a breaking API change
bumps the major version.

The **wire-protocol version is decoupled from the package version**.
`synapse_channel.core.protocol.WIRE_PROTOCOL_VERSION` (an integer, currently `2`)
changes only on a wire-incompatible change, so it is a stable compatibility
signal rather than a release counter. The hub advertises it in the `welcome`
handshake as `protocol_version` and in `/health`; a client reads the peer's
version on connect as `hub_protocol_version`. Version mismatches are accepted
with warning and negotiate down to the lowest common wire version. A peer that
omits the field is treated as legacy wire version `1`; a peer that advertises a
future version is served at the local version until this build learns the newer
capabilities.

## The stable surfaces and how they are guarded

Each stable surface is pinned by a test, so a rename, removal, or value change
fails CI rather than reaching a release:

| Surface | What is frozen | Guard |
| --- | --- | --- |
| Public Python API | the package `__all__` | `tests/test_public_api.py` |
| Wire message vocabulary | every `MessageType` name→value pair, the envelope's reserved keys, and the wire-protocol version | `tests/test_wire_surface_freeze.py` |
| Federation primitives | the deep `core.*` symbols out-of-tree consumers import — the persisted event shape, the multihub follower and its fetcher, claim forwarding, the federation store and its peer records, the TLS pin helper | `tests/test_federation_consumer_contract.py` |
| CLI surface | every subcommand is classified into a stability tier | `tests/test_surface_taxonomy.py` |
| Capability counts | class, module, wire-type, subcommand, and test counts | the capability manifest (`tools/capability_manifest.py --check`) |

The manifest pins counts, which catches an addition or removal; the freeze tests
pin identities and values, which catches a rename that keeps a count constant.
The two together close the gap either leaves open alone.

## Stability tiers

Every CLI subcommand carries a tier (`synapse_channel.surface_taxonomy`):

- **stable** — daily-safe coordination core with a stable wire and CLI surface.
- **analysis** — read-only inspection and reporting with no coordination side effects.
- **governance** — advisory governance: policy, approvals, access control, release integrity.
- **adapter** — bridges to other ecosystems and tools; optional extras, not core.
- **experimental** — newer or advisory surfaces still settling; shape may change before 1.0.

An `experimental` surface is explicitly outside the stability guarantee until it
graduates to another tier.

## Deprecation

Within `0.x`, a surface removed or changed is a reviewed edit to its guard plus a
CHANGELOG entry; a wire change also bumps `WIRE_PROTOCOL_VERSION`. After `1.0.0`,
a stable surface is removed only across a major version, with the prior behaviour
kept for a deprecation window where feasible.

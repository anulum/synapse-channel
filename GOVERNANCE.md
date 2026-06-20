<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — governance
-->

# Governance

## Project lead

SYNAPSE CHANNEL is led by **Miroslav Šotek** (ANULUM, CH & LI),
ORCID [0009-0009-3560-0851](https://orcid.org/0009-0009-3560-0851). The lead has
the final decision on scope, design, and releases.

## Decision process

- **Bug fixes and small improvements** are reviewed and merged directly.
- **New features and behaviour changes** start with an issue describing the
  problem and the proposed approach, so the design is agreed before code.
- **Wire-protocol or hub state-machine changes** require lead approval, because
  they affect every connected agent. Document the new message types in
  [`TEAM_PROTOCOL.md`](TEAM_PROTOCOL.md).
- **Security fixes** are fast-tracked (see [`SECURITY.md`](SECURITY.md)).

## Releases

Versioning follows [Semantic Versioning](https://semver.org/). Patch releases go
out as needed; minor releases gather features; a major release carries breaking
changes with a deprecation period where practical. Every release updates
[`CHANGELOG.md`](CHANGELOG.md).

## Conduct

All participants are expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md). Concerns may be reported to
`protoscience@anulum.li`.

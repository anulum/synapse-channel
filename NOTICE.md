<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — licensing and attribution notice
-->

# Notice

SYNAPSE CHANNEL is © 1998–2026 Miroslav Šotek (ANULUM, CH & LI). All rights
reserved.

## Licensing

SYNAPSE CHANNEL is dual-licensed:

- **Open source — AGPL-3.0-or-later.** You may use, study, modify, and
  redistribute the software under the terms of the GNU Affero General Public
  License version 3 or later. See [`LICENSE`](LICENSE) for the full text. The
  AGPL's network-use clause applies: if you run a modified version to provide a
  service over a network, you must offer that version's source to its users.
- **Commercial license.** A separate commercial licence is available for use
  that the AGPL's terms do not suit — for example, embedding the bus in a closed
  product without the AGPL's source-availability obligations. Contact
  `protoscience@anulum.li` for terms.

Every source file carries an SPDX `AGPL-3.0-or-later` identifier; the repository
is [REUSE](https://reuse.software/) 3.x compliant (see [`REUSE.toml`](REUSE.toml)
and [`LICENSES/`](LICENSES/)).

## Attribution

If you build on this work, attribution is appreciated. See
[`CITATION.cff`](CITATION.cff) for citation metadata.

## Third-party components

The runtime depends only on the
[`websockets`](https://pypi.org/project/websockets/) library (BSD-3-Clause). The
benchmark extra additionally uses [`tiktoken`](https://pypi.org/project/tiktoken/)
(MIT). All other functionality is built on the Python standard library. Each
dependency remains under its own licence.

The CI-only `integrations/claude-code` test package installs the exact
`@anthropic-ai/claude-code` 2.1.280 host and its platform-specific native
package from the integrity-locked npm manifest. The upstream package declares
its proprietary licence in its README; the native package carries its own
licence file. The test package manifest and lockfile are included in the source
distribution; the proprietary CLI and native binaries are not included in the
SYNAPSE CHANNEL wheel or source distribution and are not redistributed by the
release workflows.

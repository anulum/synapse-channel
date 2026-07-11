<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE GITHUB APP — licensing and attribution notice
-->

# Notice

The SYNAPSE GitHub App package is © 1998–2026 Miroslav Šotek (ANULUM, CH &
LI). All rights reserved.

## Licensing

The SYNAPSE GitHub App package is dual-licensed:

- **Open source — AGPL-3.0-or-later.** You may use, study, modify, and
  redistribute the software under the terms of the GNU Affero General Public
  License version 3 or later. See [`LICENSE`](LICENSE) for the full text. The
  AGPL's network-use clause applies: if you run a modified version to provide a
  service over a network, you must offer that version's source to its users.
- **Commercial license.** A separate commercial licence is available for use
  that the AGPL's terms do not suit — for example, embedding this adapter in a
  closed product without the AGPL's source-availability obligations. Contact
  `protoscience@anulum.li` for terms.

Every package source file carries an SPDX `AGPL-3.0-or-later` identifier. The
[upstream source repository](https://github.com/anulum/synapse-channel) is
[REUSE](https://reuse.software/) 3.x compliant.

## Attribution

If you build on this work, attribution is appreciated. See the upstream
[`CITATION.cff`](https://github.com/anulum/synapse-channel/blob/main/CITATION.cff)
for citation metadata.

## Third-party components

The package has two direct runtime dependencies:

- [`PyJWT`](https://pypi.org/project/PyJWT/) (MIT). Its `crypto` extra installs
  [`cryptography`](https://pypi.org/project/cryptography/), licensed under
  Apache-2.0 OR BSD-3-Clause.
- [`synapse-channel`](https://pypi.org/project/synapse-channel/)
  (AGPL-3.0-or-later), whose runtime dependency is
  [`websockets`](https://pypi.org/project/websockets/) (BSD-3-Clause).

No third-party code is vendored into this distribution. Direct and transitive
dependencies remain separately distributed under their own licence terms.

<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Changelog

All notable changes to this project are documented here.

## [0.3.0] - 2026-06-20

### Added
- `src/` layout installable package `synapse_channel` with a public API surface.
- Unified `synapse` console command with `hub`, `worker`, `team`, `send`, and
  `listen` subcommands.
- In-process hub + client integration test suite and an end-to-end roundtrip.
- Strict typing and NumPy-convention docstrings across every public symbol.

### Changed
- Hub routing state moved from module globals into a `SynapseHub` instance,
  allowing multiple hubs per process and deterministic testing.
- Message-envelope construction and message-type names consolidated into a single
  `protocol` module shared by the hub and client.
- Chat reply backends split into a dedicated `chat_backends` module behind a
  `ChatBackend` protocol.
- Default worker URI aligned to port 8876 across the package.
- Default worker role names changed to `FAST` and `REASON`.

### Removed
- Pre-package experimental scripts (gateways, daemons, relay bridges, terminal
  UI) moved out of the package surface pending a later hardening pass.

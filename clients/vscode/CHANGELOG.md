<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Change log

## Unreleased

- Add a read-only coordination evidence view that distinguishes self-attested
  approval and release-receipt claims, pending relay quorum, mailbox backlog,
  consume-liveness, retained delivery failures, and connection freshness.
- Add explicit evidence refresh and a versioned read-only extension API for
  integration consumers.
- Extend real token-gated Extension Host acceptance through ledger-claim,
  retained-delivery, authentication-failure, reconnect, command, and rendered
  tree-item paths.

## 0.3.0

- Add distinct own/other gutter and overview-ruler claim markers with hover
  ownership and non-colour shape cues.
- Add exact semantic-symbol scope without false whole-file fallback.
- Add strict protocol-v2 decoding, compatibility negotiation, freshness state,
  bounded reconnect, and terminal authentication/identity failure handling.
- Add canonical multi-root claim identity and exact per-file release.
- Add per-hub SecretStorage reconnect ordering and real two-hub Extension Host
  acceptance.
- Add reproducible VSIX packaging and Marketplace/Open VSX package validation.

## 0.2.0

- Add one SecretStorage credential per canonical hub URI.
- Refuse non-loopback plaintext WebSocket connections.
- Split authentication and controller responsibilities into focused modules.

## 0.1.0

- Add the status bar, shared board, current-file claim/release commands, and the
  first installable VSIX preview.

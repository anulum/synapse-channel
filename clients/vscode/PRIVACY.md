<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Extension privacy notice

The SYNAPSE CHANNEL extension contains no analytics, advertising, crash-report
upload, or third-party telemetry SDK. It sends only the coordination data needed
for its documented commands and views to the hub URI configured in the editor.

That coordination data can include the registered identity, canonical worktree,
repository-relative claimed paths, task identifiers, board state, roster state,
and protocol health frames. The configured hub controls its own retention and
logging. Ask the hub operator for that deployment's policy before connecting a
workspace that contains sensitive names or paths.

The extension delegates persistent hub-bearer storage to VS Code SecretStorage
under the canonical hub URI. Its credential wrapper does not implement its own
encryption or attest the editor's credential service. The transport holds the
bearer in memory and sends it in the first registration heartbeat on each
connection to the configured hub. Remote connections require `wss://`; plaintext
is accepted only on loopback. The extension does not write the bearer to
settings, source files, workspace files, logs, snapshots, screenshots, or
telemetry. Clearing the token through `SYNAPSE: Clear hub token` deletes that
editor-managed secret entry and reconnects without that stored token.

VS Code, Cursor, VSCodium, operating-system credential services, extension
registries, and separately operated hubs are independent products or services
and can process data under their own policies. This notice describes the
extension's source behavior; qualification of a packaged
`anulum.synapse-channel-vscode` artifact requires its exact bytes and recorded
run profile. It does not independently verify editor or operating-system
credential encryption, Marketplace artifacts or the configured hub's behavior.

Report a suspected privacy or security issue privately through the repository
[security policy](https://github.com/anulum/synapse-channel/security/policy).

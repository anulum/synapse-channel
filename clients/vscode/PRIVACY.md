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

The extension stores a hub bearer only in VS Code SecretStorage under the
canonical hub URI. It does not write the bearer to settings, source files,
workspace files, logs, snapshots, screenshots, or telemetry. Clearing the token
through `SYNAPSE: Clear hub token` deletes that editor-managed secret entry.

VS Code, Cursor, VSCodium, operating-system credential services, extension
registries, and separately operated hubs are independent products or services
and can process data under their own policies. This notice covers only the code
shipped in `anulum.synapse-channel-vscode`.

Report a suspected privacy or security issue privately through the repository
[security policy](https://github.com/anulum/synapse-channel/security/policy).

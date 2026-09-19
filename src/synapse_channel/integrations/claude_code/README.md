<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Synapse Channel for Claude Code

This optional plugin provides the Synapse MCP tools and a claim-aware
`PreToolUse` hook for `Edit`, `Write`, and `Bash`. It uses the installed
`synapse-channel[mcp]` executable. Configure and install it with
`synapse adapters claude-plugin`; see the Core documentation for exact
identity, hub, compatibility and removal instructions.

The claim hook is cooperative. Unknown shell effects are denied or require
separate authority; it does not provide operating-system-enforced write
isolation. Project and provider instructions remain lower trust than the
owner's explicit approval and hub claim state.

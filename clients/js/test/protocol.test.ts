// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — typed peer attachment envelope compatibility

import { expect, it } from "vitest";
import {
  MessageType, MIN_ATTACHMENT_PEER_PROTOCOL_VERSION, buildEnvelope,
} from "../src/protocol.js";

it("builds the recipient-bound version-six request without changing local attachment verbs", () => {
  const request = buildEnvelope("hub-recipient", MessageType.AttachmentPeerRequest, {
    target: "SynapseHub",
    now: 100,
    extra: {
      action: "read", scope: "PROJECT", digest: "a".repeat(64), offset: 32768,
      protocol_version: MIN_ATTACHMENT_PEER_PROTOCOL_VERSION,
    },
  });
  expect(request).toEqual({
    sender: "hub-recipient", target: "SynapseHub", type: "attachment_peer_request",
    payload: "", timestamp: 100, action: "read", scope: "PROJECT",
    digest: "a".repeat(64), offset: 32768, protocol_version: 6,
  });
  expect(MessageType.AttachmentPeerResult).toBe("attachment_peer_result");
  expect(MessageType.AttachmentRead).toBe("attachment_read");
});

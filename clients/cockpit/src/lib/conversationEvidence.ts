// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — exact selected-message evidence chain

import type { ConversationMessage } from "./communications";

export interface ConversationEvidence {
  readonly message: ConversationMessage;
  readonly responses: readonly ConversationMessage[];
}

/** Join only exact response-to-sequence links within one bounded conversation. */
export function conversationEvidenceFor(
  messages: readonly ConversationMessage[],
  messageSeq: number,
): ConversationEvidence | null {
  const message = messages.find((candidate) => candidate.seq === messageSeq);
  if (message === undefined) return null;
  return {
    message,
    responses: messages
      .filter((candidate) => candidate.responseToSeq === messageSeq)
      .sort((left, right) => left.seq - right.seq),
  };
}

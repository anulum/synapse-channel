// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — safe editor projection of hub close events

/** Classify hub close events without reflecting peer-controlled reasons. */

/** Editor-safe close handling decision. */
export type HubCloseDecision =
  | { kind: "identity-mismatch" }
  | { kind: "terminal"; warning: string }
  | { kind: "retry"; warning?: string };

/** Map private hub close codes to bounded, non-reflective UI state. */
export function decideHubClose(event: Pick<CloseEvent, "code" | "reason">): HubCloseDecision {
  if (event.code === 4013 && event.reason.toLowerCase().includes("identity pin mismatch")) {
    return { kind: "identity-mismatch" };
  }
  if (event.code === 4010 || event.code === 4003) {
    return {
      kind: "terminal",
      warning: "Hub authentication or seat ownership was refused.",
    };
  }
  if (event.code === 4009 || event.code === 4016) {
    return {
      kind: "terminal",
      warning: "Hub seat ownership was refused.",
    };
  }
  if (event.code === 4013) {
    return { kind: "retry", warning: "Hub refused the connection." };
  }
  return { kind: "retry" };
}

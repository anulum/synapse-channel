// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — one served operation chain, in the shape `/operations.json?operation=ID` gives

/** A verified operation's chain with a link of every kind but the seal. */
export const CHAIN = {
  operation_id: "op-verified",
  task_id: "T1",
  state: "verified",
  intent_sha256: "a".repeat(64),
  digest_check: "matches",
  attempts: [{ attempt_id: "att-1", executor: "P/worker", state: "verified" }],
  chain: [
    { seq: 4, at: 1_800_000_000.5, kind: "declared", actor: "P/worker", detail: { effect: "file", intent_sha256: "a".repeat(64) } },
    { seq: 6, at: 1_800_000_001, kind: "approval", actor: "P/approver", detail: { state: "approved", reason: "" } },
    { seq: 7, at: 1_800_000_002, kind: "answer", actor: "P/worker", detail: { answer: "approved_fresh", attempt_id: "att-1" } },
    { seq: 8, at: 1_800_000_003, kind: "outcome", actor: "P/worker", detail: { state: "confirmed_done", external_effect: true, boundary: { at: 2 } } },
    { seq: 9, at: 1_800_000_004, kind: "verification", actor: "P/checker", detail: { result: "verified", effect: null } },
  ],
  as_of_seq: 40,
  as_of: 1_800_000_010,
  custody: "not recorded here",
  present: true,
};

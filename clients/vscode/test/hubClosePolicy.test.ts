// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for editor-safe hub close classification

import { describe, expect, it } from "vitest";
import { decideHubClose } from "../src/hubClosePolicy.js";

describe("decideHubClose", () => {
  it("distinguishes an identity pin mismatch without reflecting the reason", () => {
    expect(decideHubClose({ code: 4013, reason: "IDENTITY PIN MISMATCH secret" })).toEqual({
      kind: "identity-mismatch",
    });
  });

  it.each([4010, 4003])("projects authentication close code %i", (code) => {
    expect(decideHubClose({ code, reason: "peer detail" })).toEqual({
      kind: "terminal",
      warning: "Hub authentication or seat ownership was refused.",
    });
  });

  it.each([4009, 4016])("stops on seat-ownership close code %i", (code) => {
    expect(decideHubClose({ code, reason: "private peer detail" })).toEqual({
      kind: "terminal",
      warning: "Hub seat ownership was refused.",
    });
  });

  it("projects another trust close without exposing its reason", () => {
    expect(decideHubClose({ code: 4013, reason: "private refusal" })).toEqual({
      kind: "retry",
      warning: "Hub refused the connection.",
    });
  });

  it("retries an ordinary close without inventing a warning", () => {
    expect(decideHubClose({ code: 1006, reason: "network detail" })).toEqual({ kind: "retry" });
  });
});

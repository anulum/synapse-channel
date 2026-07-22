// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — contextual guide model tests

import { describe, expect, it } from "vitest";

import { filterGuideTopics, guideTopics } from "../src/lib/guide";
import { formatMessage } from "../src/lib/i18n";

describe("cockpit guide model", () => {
  const english = guideTopics((key, values) => formatMessage("en", key, values), "fleet");

  it("puts the active panel first and follows with the stable reference topics", () => {
    expect(english).toHaveLength(7);
    expect(english[0]?.id).toBe("panel-fleet");
    expect(english[0]?.title).toBe("Fleet views");
    expect(english.map((topic) => topic.id).slice(1)).toEqual([
      "orientation",
      "limits",
      "actions",
      "shortcuts",
      "troubleshooting",
      "setup",
    ]);
  });

  it("returns all topics for an empty query and searches all local topic text", () => {
    expect(filterGuideTopics(english, "  ", "en")).toBe(english);
    expect(filterGuideTopics(english, "  RECEIPTS ", "en").map((topic) => topic.id)).toEqual([
      "actions",
    ]);
    expect(filterGuideTopics(english, "no such guide phrase", "en")).toEqual([]);
  });

  it("resolves Slovak contextual text without changing the panel identifier", () => {
    const slovak = guideTopics((key, values) => formatMessage("sk", key, values), "causality");
    expect(slovak[0]?.id).toBe("panel-causality");
    expect(slovak[0]?.title).toBe("Kauzalita");
  });
});

// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — log-export document tests

import { describe, expect, it } from "vitest";
import { buildLogExport, exportFilename } from "../src/lib/exportLog";
import { OPEN_QUERY } from "../src/lib/logQuery";
import type { CockpitEvent } from "../src/types";

const EVENT: CockpitEvent = {
  seq: 5357,
  ts: 1783071633.9,
  kind: "release",
  lane: "claims",
  severity: 0.5,
  actor: "",
  label: "released t",
  taskId: "t",
  payload: { task_id: "t" },
};

describe("buildLogExport", () => {
  it("states provenance, query, window, and coverage alongside the events", () => {
    const exported = buildLogExport(
      [EVENT],
      "hub",
      { ...OPEN_QUERY, text: "release" },
      { fromTs: 100, toTs: 200 },
      1_783_071_700_000,
    );
    expect(exported).toEqual({
      exported_at: new Date(1_783_071_700_000).toISOString(),
      provenance: "hub",
      query: { ...OPEN_QUERY, text: "release" },
      window: { fromTs: 100, toTs: 200 },
      count: 1,
      events: [EVENT],
    });
    // Payload passes through verbatim — the attested material is the point.
    expect(exported.events[0]?.payload).toEqual({ task_id: "t" });
  });

  it("handles the unwindowed empty view", () => {
    const exported = buildLogExport([], "derived", OPEN_QUERY, null, 0);
    expect(exported.count).toBe(0);
    expect(exported.window).toBeNull();
  });
});

describe("exportFilename", () => {
  it("stamps provenance and a filesystem-safe timestamp", () => {
    const name = exportFilename("hub", 1_783_071_700_000);
    expect(name).toMatch(/^cockpit-events-hub-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.json$/);
  });
});

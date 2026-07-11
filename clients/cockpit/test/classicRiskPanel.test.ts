// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — classic cockpit risk-panel DOM security regression

// @vitest-environment jsdom

/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, it } from "vitest";

type RiskPanel = {
  render(snapshot: unknown): void;
};

const panelSource = readFileSync(
  resolve(process.cwd(), "../../src/synapse_channel/dashboard_assets/risk-panel.js"),
  "utf8",
);

function loadRiskPanel(): RiskPanel {
  document.body.innerHTML = '<span id="risk-verdict"></span><div id="risk"></div>';
  window.eval(panelSource);
  return (window as typeof window & { SynapseRiskPanel: RiskPanel }).SynapseRiskPanel;
}

it("keeps quote-bearing hints inside title attributes", () => {
  const payload = `candidate" onmouseover="window.__synapseXss = true' data-x='payload`;
  loadRiskPanel().render({
    risk: {
      level: "green",
      signals: [],
      safe_next_work: ["RICH", "COMPACT"],
      guidance: {
        omitted_tasks: 0,
        trust_boundary: "advisory only",
        tasks: [
          {
            task_id: "RICH",
            route_candidates: [{ agent: "route", score: 1, reasons: [payload] }],
            resource_bids: [
              {
                agent: "resource",
                capacity: 1,
                reasons: [payload],
                resource_kind: "gpu",
                resource_name: "A100",
              },
            ],
          },
          {
            task_id: "COMPACT",
            route_candidates: [],
            resource_bids: [],
            route_fallback: payload,
            resource_fallback: payload,
          },
        ],
      },
    },
  });

  const titled = Array.from(document.querySelectorAll<HTMLElement>("#risk [title]"));
  expect(titled.map((element) => element.title)).toEqual([payload, payload, `${payload}; ${payload}`]);
  expect(document.querySelector("#risk [onmouseover]")).toBeNull();
});

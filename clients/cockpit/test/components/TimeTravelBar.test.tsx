// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — live, historical, and comparison boundary tests

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TimeTravelBar } from "../../src/components/TimeTravelBar";

afterEach(cleanup);

describe("TimeTravelBar", () => {
  it("names the active mode with text and aria state", () => {
    render(<TimeTravelBar mode="history" label="HISTORY B · roster stays live" onModeChange={() => {}} />);
    expect(screen.getByRole("status").textContent).toContain("HISTORY B");
    expect(screen.getByRole("button", { name: "history" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "live" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("offers all three time modes through one labelled control group", async () => {
    const onModeChange = vi.fn();
    render(<TimeTravelBar mode="live" label="LIVE" onModeChange={onModeChange} />);
    expect(screen.getByRole("group", { name: "Fleet evidence time mode" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "compare" }));
    await userEvent.click(screen.getByRole("button", { name: "history" }));
    await userEvent.click(screen.getByRole("button", { name: "live" }));
    expect(onModeChange.mock.calls.map(([mode]) => mode)).toEqual(["compare", "history", "live"]);
  });
});

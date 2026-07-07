// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — time-travel bar behaviour tests

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TimeTravelBar } from "../../src/components/TimeTravelBar";
import type { FleetStateAt } from "../../src/lib/stateAt";

afterEach(cleanup);

const MOMENT: FleetStateAt = {
  asOfSeq: 42,
  logEndSeq: 100,
  asOfTs: 1_751_800_000,
  note: "presence not journalled",
  claims: [],
  tasks: [],
};

describe("TimeTravelBar", () => {
  it("offers arming and hides the scrubber while off", () => {
    render(
      <TimeTravelBar on={false} seq={1} state={null} note={null} onToggle={() => {}} onScrub={() => {}} />,
    );
    expect(screen.getByText("time travel")).toBeTruthy();
    expect(screen.getByRole("button").getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByRole("slider")).toBeNull();
  });

  it("states the reconstructed moment and its honest scope when armed", () => {
    render(
      <TimeTravelBar on seq={42} state={MOMENT} note={null} onToggle={() => {}} onScrub={() => {}} />,
    );
    expect(screen.getByText("back to now")).toBeTruthy();
    const label = screen.getByText(/claims \+ board as of seq 42/);
    expect(label.textContent).toContain("roster stays live (presence not journalled)");
    const slider = screen.getByRole("slider") as HTMLInputElement;
    expect(slider.max).toBe("100");
    expect(slider.value).toBe("42");
  });

  it("says reconstructing before the first fetch lands and states a fetch note verbatim", () => {
    render(
      <TimeTravelBar on seq={7} state={null} note={null} onToggle={() => {}} onScrub={() => {}} />,
    );
    expect(screen.getByText("reconstructing…")).toBeTruthy();
    cleanup();
    render(
      <TimeTravelBar
        on
        seq={7}
        state={null}
        note="state-at surface not served (--feeds-db)"
        onToggle={() => {}}
        onScrub={() => {}}
      />,
    );
    expect(screen.getByText("state-at surface not served (--feeds-db)")).toBeTruthy();
  });

  it("scrubs by sequence and toggles the mode", async () => {
    const onScrub = vi.fn();
    const onToggle = vi.fn();
    render(
      <TimeTravelBar on seq={42} state={MOMENT} note={null} onToggle={onToggle} onScrub={onScrub} />,
    );
    fireEvent.change(screen.getByRole("slider"), { target: { value: "17" } });
    expect(onScrub).toHaveBeenCalledWith(17);
    await userEvent.click(screen.getByText("back to now"));
    expect(onToggle).toHaveBeenCalled();
  });

  it("shows a dash for a moment with no timestamp of its own", () => {
    render(
      <TimeTravelBar
        on
        seq={1}
        state={{ ...MOMENT, asOfSeq: 1, asOfTs: 0 }}
        note={null}
        onToggle={() => {}}
        onScrub={() => {}}
      />,
    );
    expect(screen.getByText(/as of seq 1 · —/)).toBeTruthy();
  });
});

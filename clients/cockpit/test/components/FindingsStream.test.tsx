// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — findings stream behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FindingsStream } from "../../src/components/FindingsStream";

afterEach(cleanup);

describe("FindingsStream", () => {
  it("distinguishes 'not connected yet' from 'connected and empty'", () => {
    render(<FindingsStream findings={[]} connected={false} />);
    expect(screen.getByText("Waiting for the hub.")).toBeTruthy();
    cleanup();
    render(<FindingsStream findings={[]} connected />);
    expect(screen.getByText("No findings recorded.")).toBeTruthy();
  });

  it("shows time, author, task, and text, with dashes for the unrecorded", () => {
    render(
      <FindingsStream
        connected
        findings={[
          { taskId: "t-1", postedAt: 1_751_800_000, author: "quantum/claude", text: "root cause found" },
          { taskId: "", postedAt: null, author: "", text: "unattributed note" },
        ]}
      />,
    );
    expect(screen.getByText("root cause found")).toBeTruthy();
    expect(screen.getByText("quantum/claude")).toBeTruthy();
    expect(screen.getByText("t-1")).toBeTruthy();
    expect(screen.getByText("unattributed note")).toBeTruthy();
    expect(screen.getAllByText("—")).toHaveLength(2);
  });
});

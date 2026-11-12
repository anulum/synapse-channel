// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — identifier element behaviour tests

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Ident } from "../../src/components/Ident";

afterEach(cleanup);

const SEAT = "harbour-coastal-monitoring-programme/autonomous-operations-seat-api";

describe("Ident", () => {
  it("renders the whole value as one element's text, in a head and a tail", () => {
    const { container } = render(<Ident value={SEAT} className="claim-row__owner" />);
    const element = container.querySelector(".ident")!;

    expect(element.textContent).toBe(SEAT);
    expect(element.getAttribute("title")).toBe(SEAT);
    expect(element.classList.contains("claim-row__owner")).toBe(true);
    expect(element.querySelector(".ident__head")!.textContent).toBe(
      "harbour-coastal-monitoring-programme/autonomous-operations-seat",
    );
    expect(element.querySelector(".ident__tail")!.textContent).toBe("-api");
    expect(element.hasAttribute("tabindex")).toBe(false);
  });

  it("renders a short value without a tail and without an extra class", () => {
    const { container } = render(<Ident value="api" />);
    const element = container.querySelector(".ident")!;

    expect(element.className).toBe("ident");
    expect(element.textContent).toBe("api");
    expect(element.querySelector(".ident__tail")).toBeNull();
  });

  it("takes focus and answers the copy key with the whole value when asked to", () => {
    const { container } = render(<Ident value={SEAT} focusable />);
    const element = container.querySelector<HTMLElement>(".ident")!;
    expect(element.getAttribute("tabindex")).toBe("0");

    const copied: Record<string, string> = {};
    const clipboardData = { setData: (type: string, text: string) => { copied[type] = text; } };
    const untouched = fireEvent.copy(element, { clipboardData });

    expect(copied).toEqual({ "text/plain": SEAT });
    expect(untouched).toBe(false);
  });

  it("leaves a selection the reader made to the browser", () => {
    const { container } = render(<Ident value={SEAT} focusable />);
    const element = container.querySelector<HTMLElement>(".ident")!;
    const selection = window.getSelection()!;
    const range = document.createRange();
    range.selectNodeContents(element.querySelector(".ident__tail")!);
    selection.removeAllRanges();
    selection.addRange(range);

    const copied: Record<string, string> = {};
    const clipboardData = { setData: (type: string, text: string) => { copied[type] = text; } };
    const untouched = fireEvent.copy(element, { clipboardData });

    expect(copied).toEqual({});
    expect(untouched).toBe(true);
    selection.removeAllRanges();
  });
});

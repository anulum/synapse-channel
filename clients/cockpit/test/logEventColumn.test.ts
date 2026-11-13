// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the signal log keeps a readable event column on a desktop

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("signal log event column", () => {
  it("gives the event a readable width above a phone and still drops the lane there", () => {
    const css = readFileSync(new URL("../src/styles/global.css", import.meta.url), "utf8");
    const desktopAt = css.indexOf("@media (min-width: 641px)");
    expect(desktopAt).toBeGreaterThanOrEqual(0);
    const nextMedia = css.indexOf("@media", desktopAt + 10);
    const desktop = css.slice(desktopAt, nextMedia === -1 ? undefined : nextMedia);
    expect(desktop).toContain("table-layout: fixed");
    expect(desktop).toContain(".log__row td.log__label");
    expect(desktop).toMatch(/min-width:\s*14ch/);
    const phoneAt = css.indexOf("@media (max-width: 720px)");
    const phone = css.slice(phoneAt, css.indexOf("}", css.indexOf(".log__lane", phoneAt)) + 1);
    expect(phone).toContain(".log__lane");
    expect(phone).toContain("display: none");
  });
});

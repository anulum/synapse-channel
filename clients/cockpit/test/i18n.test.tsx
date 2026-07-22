// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — typed cockpit localisation tests

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { CockpitI18nProvider, useCockpitI18n } from "../src/context/CockpitI18n";
import {
  CATALOGUES,
  formatCatalogueMessage,
  formatMessage,
  LOCALE_PREFERENCE_KEY,
  resolveLocale,
  searchWithLocale,
} from "../src/lib/i18n";

afterEach(() => {
  cleanup();
  localStorage.clear();
  history.replaceState(null, "", "/cockpit/");
  document.documentElement.removeAttribute("lang");
});

function Probe(): React.JSX.Element {
  const { locale, setLocale, t } = useCockpitI18n();
  return (
    <div>
      <output aria-label="locale">{locale}</output>
      <output>{t("hud.transport", { status: "gap detected" })}</output>
      <button type="button" onClick={() => setLocale(locale === "en" ? "sk" : "en")}>switch</button>
    </div>
  );
}

describe("cockpit localisation", () => {
  it("keeps the EN and SK catalogues in exact key parity", () => {
    expect(Object.keys(CATALOGUES.sk).sort()).toEqual(Object.keys(CATALOGUES.en).sort());
  });

  it("resolves URL, stored preference, browser locale, then English in that order", () => {
    expect(resolveLocale("?lang=sk", "en", ["en-US"])).toBe("sk");
    expect(resolveLocale("?lang=de", "SK-sk", ["en-US"])).toBe("sk");
    expect(resolveLocale("", "de", ["de-CH", "sk-SK"])).toBe("sk");
    expect(resolveLocale("", null, ["de-CH"])).toBe("en");
  });

  it("preserves unrelated URL state when setting a locale", () => {
    expect(searchWithLocale("?panel=fleet&task=T-1", "sk")).toBe("?panel=fleet&task=T-1&lang=sk");
    expect(searchWithLocale("", "en")).toBe("?lang=en");
  });

  it("formats values, preserves unknown placeholders, and falls back to English", () => {
    expect(formatMessage("sk", "hud.transport", { status: "stream" })).toBe("Živý transport: stream");
    expect(formatMessage("en", "hud.transport")).toBe("Live transport: {status}");
    expect(formatCatalogueMessage({}, "hud.live")).toBe("live");
  });

  it("persists a choice, updates the URL and html lang, and follows history navigation", async () => {
    history.replaceState(null, "", "/cockpit/?panel=fleet&lang=sk");
    render(<CockpitI18nProvider><Probe /></CockpitI18nProvider>);
    expect(screen.getByLabelText("locale").textContent).toBe("sk");
    expect(document.documentElement.lang).toBe("sk");

    await userEvent.click(screen.getByRole("button", { name: "switch" }));
    expect(screen.getByLabelText("locale").textContent).toBe("en");
    expect(localStorage.getItem(LOCALE_PREFERENCE_KEY)).toBe("en");
    expect(location.search).toContain("panel=fleet");
    expect(location.search).toContain("lang=en");

    history.pushState(null, "", "/cockpit/?lang=sk");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(await screen.findByText("Živý transport: gap detected")).toBeTruthy();
  });
});

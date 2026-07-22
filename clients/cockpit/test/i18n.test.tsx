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
  type MessageKey,
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
  it("keeps every translated catalogue in exact key parity", () => {
    expect(Object.keys(CATALOGUES.sk).sort()).toEqual(Object.keys(CATALOGUES.en).sort());
    expect(Object.keys(CATALOGUES.de).sort()).toEqual(Object.keys(CATALOGUES.en).sort());
    expect(Object.keys(CATALOGUES.es).sort()).toEqual(Object.keys(CATALOGUES.en).sort());
    expect(Object.keys(CATALOGUES.fr).sort()).toEqual(Object.keys(CATALOGUES.en).sort());
  });

  it("keeps interpolation placeholders identical in every catalogue", () => {
    const placeholders = (value: string): readonly string[] =>
      [...value.matchAll(/\{([A-Za-z0-9_]+)\}/gu)].map((match) => match[1] ?? "").sort();
    const keys = Object.keys(CATALOGUES.en) as MessageKey[];
    for (const catalogue of Object.values(CATALOGUES)) {
      for (const key of keys) {
        expect(placeholders(catalogue[key]), key).toEqual(placeholders(CATALOGUES.en[key]));
      }
    }
  });

  it("resolves URL, stored preference, browser locale, then English in that order", () => {
    expect(resolveLocale("?lang=sk", "en", ["en-US"])).toBe("sk");
    expect(resolveLocale("?lang=de", "sk", ["en-US"])).toBe("de");
    expect(resolveLocale("?lang=es", "de", ["en-US"])).toBe("es");
    expect(resolveLocale("?lang=fr", "SK-sk", ["en-US"])).toBe("fr");
    expect(resolveLocale("?lang=it", "FR-fr", ["de-CH"])).toBe("fr");
    expect(resolveLocale("", null, ["es-MX", "en-US"])).toBe("es");
    expect(resolveLocale("", null, ["fr-CH", "en-US"])).toBe("fr");
    expect(resolveLocale("", null, ["it-CH"])).toBe("en");
  });

  it("preserves unrelated URL state when setting a locale", () => {
    expect(searchWithLocale("?panel=fleet&task=T-1", "sk")).toBe("?panel=fleet&task=T-1&lang=sk");
    expect(searchWithLocale("", "en")).toBe("?lang=en");
  });

  it("formats values, preserves unknown placeholders, and falls back to English", () => {
    expect(formatMessage("sk", "hud.transport", { status: "stream" })).toBe("Živý transport: stream");
    expect(formatMessage("de", "hud.transport", { status: "gap detected" })).toBe("Live-Transport: gap detected");
    expect(formatMessage("es", "hud.transport", { status: "poll fallback" })).toBe("Transporte en directo: poll fallback");
    expect(formatMessage("fr", "hud.transport", { status: "poll fallback" })).toBe("Transport en direct : poll fallback");
    expect(formatMessage("en", "hud.transport")).toBe("Live transport: {status}");
    expect(formatCatalogueMessage({}, "hud.live")).toBe("live");
  });

  it("keeps translated protocol outcomes and setup placeholders literal", () => {
    for (const locale of ["de", "es", "fr"] as const) {
      const outcomes = formatMessage(locale, "guide.topic.actions.body");
      for (const token of [
        "accepted",
        "delivered",
        "undelivered",
        "denied",
        "rejected",
        "rate-limited",
        "unreachable",
      ]) expect(outcomes).toContain(token);
      expect(formatMessage(locale, "setup.profile.durableHelp")).toContain("<HUB_DB_PATH>");
      expect(formatMessage(locale, "setup.profile.protectedHelp")).toContain("<OWNER_ONLY_ACCESS_POLICY_PATH>");
    }
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

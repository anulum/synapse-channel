// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit locale selection and message formatting

import {
  ENGLISH,
  type Catalogue,
  type MessageKey,
} from "./i18n/catalogues/english";
import { FRENCH } from "./i18n/catalogues/french";
import { GERMAN } from "./i18n/catalogues/german";
import { SLOVAK } from "./i18n/catalogues/slovak";
import { SPANISH } from "./i18n/catalogues/spanish";

export const SUPPORTED_LOCALES = ["en", "sk", "de", "es", "fr"] as const;
export type CockpitLocale = (typeof SUPPORTED_LOCALES)[number];
export type { MessageKey };

export const LOCALE_PREFERENCE_KEY = "cockpit-locale";

export const CATALOGUES: Readonly<Record<CockpitLocale, Catalogue>> = {
  en: ENGLISH,
  sk: SLOVAK,
  de: GERMAN,
  es: SPANISH,
  fr: FRENCH,
};

function normaliseLocale(candidate: string): CockpitLocale | null {
  const base = candidate.trim().toLowerCase().split("-")[0];
  return base === "en" || base === "sk" || base === "de" || base === "es" || base === "fr"
    ? base
    : null;
}

export function resolveLocale(
  search: string,
  stored: string | null,
  browserLanguages: readonly string[],
): CockpitLocale {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const explicit = normaliseLocale(params.get("lang") ?? "");
  if (explicit !== null) return explicit;
  const preferred = normaliseLocale(stored ?? "");
  if (preferred !== null) return preferred;
  for (const candidate of browserLanguages) {
    const locale = normaliseLocale(candidate);
    if (locale !== null) return locale;
  }
  return "en";
}

export function searchWithLocale(search: string, locale: CockpitLocale): string {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  params.set("lang", locale);
  return `?${params.toString()}`;
}

export function formatMessage(
  locale: CockpitLocale,
  key: MessageKey,
  values: Readonly<Record<string, string | number>> = {},
): string {
  return formatCatalogueMessage(CATALOGUES[locale], key, values);
}

/** Runtime fallback protects partial future catalogues while compile-time parity stays strict today. */
export function formatCatalogueMessage(
  catalogue: Partial<Catalogue>,
  key: MessageKey,
  values: Readonly<Record<string, string | number>> = {},
): string {
  const template = catalogue[key] ?? ENGLISH[key];
  return template.replace(/\{([A-Za-z0-9_]+)\}/gu, (match, name: string) =>
    Object.hasOwn(values, name) ? String(values[name]) : match,
  );
}

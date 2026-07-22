// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit localisation provider

import type { JSX, ReactNode } from "react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import {
  formatMessage,
  LOCALE_PREFERENCE_KEY,
  resolveLocale,
  searchWithLocale,
  type CockpitLocale,
  type MessageKey,
} from "../lib/i18n";
import { readPref, writePref } from "../lib/prefs";

interface I18nContextValue {
  readonly locale: CockpitLocale;
  readonly setLocale: (locale: CockpitLocale) => void;
  readonly t: (key: MessageKey, values?: Readonly<Record<string, string | number>>) => string;
}

const DEFAULT_CONTEXT: I18nContextValue = {
  locale: "en",
  setLocale: () => undefined,
  t: (key, values) => formatMessage("en", key, values),
};

const I18nContext = createContext<I18nContextValue>(DEFAULT_CONTEXT);

function resolvedBrowserLocale(): CockpitLocale {
  return resolveLocale(
    location.search,
    readPref(localStorage, LOCALE_PREFERENCE_KEY),
    navigator.languages,
  );
}

export function CockpitI18nProvider({ children }: { readonly children: ReactNode }): JSX.Element {
  const [locale, setLocaleState] = useState<CockpitLocale>(resolvedBrowserLocale);

  useEffect(() => {
    const onPopState = (): void => setLocaleState(resolvedBrowserLocale());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((next: CockpitLocale) => {
    writePref(localStorage, LOCALE_PREFERENCE_KEY, next);
    const nextSearch = searchWithLocale(location.search, next);
    history.pushState(history.state, "", `${location.pathname}${nextSearch}${location.hash}`);
    setLocaleState(next);
  }, []);

  const t = useCallback(
    (key: MessageKey, values: Readonly<Record<string, string | number>> = {}) =>
      formatMessage(locale, key, values),
    [locale],
  );
  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useCockpitI18n(): I18nContextValue {
  return useContext(I18nContext);
}

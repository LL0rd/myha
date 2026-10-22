import { useSyncExternalStore } from "react";
import de from "./de.json";
import en from "./en.json";

export const langs = ["en", "de"] as const;
export type Lang = (typeof langs)[number];
export type Vars = Record<string, string | number>;

const dicts: Record<Lang, Record<string, string>> = { en, de };
const KEY = "myha.lang";

const isLang = (v: unknown): v is Lang => langs.indexOf(v as Lang) >= 0;

/** Stored choice, else the browser language (de-* → de), else English. */
export function detectLang(): Lang {
  try {
    const stored = localStorage.getItem(KEY);
    if (isLang(stored)) return stored;
  } catch {
    // storage blocked (private mode): fall through
  }
  return /^de\b/i.test(navigator.language || "") ? "de" : "en";
}

/** The text for `key` with `{name}` placeholders filled; the key itself when no text exists. */
export function translate(lang: Lang, key: string, vars?: Vars): string {
  const text = dicts[lang][key] ?? key;
  return vars ? text.replace(/\{(\w+)\}/g, (m, name: string) => (name in vars ? String(vars[name]) : m)) : text;
}

// One language for the whole page, shared by every useI18n caller without a provider.
let current: Lang | null = null;
const listeners = new Set<() => void>();
function getLang(): Lang {
  if (current === null) {
    current = detectLang();
    document.documentElement.lang = current;
  }
  return current;
}
const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};

export function setLang(lang: Lang) {
  current = lang;
  try {
    localStorage.setItem(KEY, lang);
  } catch {
    // not persisted in private mode; the choice holds for this page
  }
  document.documentElement.lang = lang;
  listeners.forEach((fn) => fn());
}

export function useI18n() {
  const lang = useSyncExternalStore(subscribe, getLang);
  return { lang, setLang, t: (key: string, vars?: Vars) => translate(lang, key, vars) };
}

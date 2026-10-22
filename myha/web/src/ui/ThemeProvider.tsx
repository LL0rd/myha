import { useSyncExternalStore } from "react";

export const themes = ["system", "light", "dark", "contrast"] as const;
export type Theme = (typeof themes)[number];
export type ResolvedTheme = Exclude<Theme, "system">;

const KEY = "myha.theme";
const QUERY = "(prefers-color-scheme: dark)";

const media = () => (typeof window.matchMedia === "function" ? window.matchMedia(QUERY) : null);
const systemTheme = (): ResolvedTheme => (media()?.matches ? "dark" : "light");
function subscribeSystem(onChange: () => void) {
  const mq = media();
  mq?.addEventListener("change", onChange);
  return () => mq?.removeEventListener("change", onChange);
}

function stored(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v !== "system" && themes.indexOf(v as Theme) >= 0 ? (v as Theme) : "system";
  } catch {
    return "system"; // storage blocked (private mode)
  }
}

// "system" leaves <html> without data-theme, so tokens.css follows the OS live.
function apply(theme: Theme) {
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

// One theme for the whole page, like the language in src/i18n: no provider, every caller shares it.
let current: Theme | null = null;
const listeners = new Set<() => void>();
function getTheme(): Theme {
  if (current === null) {
    current = stored();
    apply(current);
  }
  return current;
}
function subscribe(onChange: () => void) {
  listeners.add(onChange);
  return () => listeners.delete(onChange);
}

export function setTheme(next: Theme) {
  current = next;
  apply(next);
  try {
    if (next === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, next);
  } catch {
    // not persisted in private mode; the choice holds for this page
  }
  listeners.forEach((fn) => fn());
}

/** `theme` is the choice ("system" while nothing is stored); `resolved` is what is shown. */
export function useTheme(): { theme: Theme; resolved: ResolvedTheme; setTheme: (theme: Theme) => void } {
  const theme = useSyncExternalStore(subscribe, getTheme);
  const system = useSyncExternalStore(subscribeSystem, systemTheme);
  return { theme, resolved: theme === "system" ? system : theme, setTheme };
}

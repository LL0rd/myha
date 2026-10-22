import { createContext, useContext, useState, useSyncExternalStore, type ReactNode } from "react";

export const themes = ["system", "light", "dark", "contrast"] as const;
export type Theme = (typeof themes)[number];
export type ResolvedTheme = Exclude<Theme, "system">;

interface ThemeState {
  /** The choice; "system" while nothing is stored. */
  theme: Theme;
  /** What is shown: the stored choice, else the system preference. */
  resolved: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeState | null>(null);
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

function useThemeState(): ThemeState {
  const [theme, set] = useState<Theme>(() => {
    const choice = stored();
    apply(choice);
    return choice;
  });
  const system = useSyncExternalStore(subscribeSystem, systemTheme);
  const setTheme = (next: Theme) => {
    apply(next);
    try {
      if (next === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {
      // not persisted in private mode; the choice holds for this page
    }
    set(next);
  };
  return { theme, resolved: theme === "system" ? system : theme, setTheme };
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  return <ThemeContext.Provider value={useThemeState()}>{children}</ThemeContext.Provider>;
}

/** The shared theme; without a ThemeProvider above, a theme state of the caller's own. */
export function useTheme(): ThemeState {
  const own = useThemeState();
  return useContext(ThemeContext) ?? own;
}

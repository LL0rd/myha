import { createContext, useContext, useState, type ReactNode } from "react";

export const themes = ["light", "dark", "contrast"] as const;
export type Theme = (typeof themes)[number];

interface ThemeState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeState | null>(null);
const KEY = "myha.theme";

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return themes.indexOf(v as Theme) >= 0 ? (v as Theme) : null;
  } catch {
    return null; // storage blocked (private mode)
  }
}

const systemTheme = (): Theme =>
  typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";

const apply = (theme: Theme) => {
  document.documentElement.dataset.theme = theme;
};

// Stored choice, else the system preference. Without a choice <html> carries no data-theme, so tokens.css follows the system live.
function useThemeState(): ThemeState {
  const [theme, set] = useState<Theme>(() => {
    const choice = stored();
    if (choice) apply(choice);
    return choice ?? systemTheme();
  });
  const setTheme = (next: Theme) => {
    apply(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // not persisted in private mode; the choice holds for this page
    }
    set(next);
  };
  return { theme, setTheme };
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  return <ThemeContext.Provider value={useThemeState()}>{children}</ThemeContext.Provider>;
}

/** The shared theme; without a ThemeProvider above, a theme state of the caller's own. */
export function useTheme(): ThemeState {
  const own = useThemeState();
  return useContext(ThemeContext) ?? own;
}

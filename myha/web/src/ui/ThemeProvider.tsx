import { createContext, useContext, useState, type ReactNode } from "react";

export const themes = ["system", "light", "dark", "contrast"] as const;
export type Theme = (typeof themes)[number];

interface ThemeState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

// tokens.css follows the system preference unless <html data-theme> names a theme.
function useThemeState(): ThemeState {
  const [theme, set] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme | undefined) ?? "system");
  const setTheme = (next: Theme) => {
    if (next === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = next;
    set(next);
  };
  return { theme, setTheme };
}

// shortcut: no persistence yet; Task 8.3 stores the choice.
export function ThemeProvider({ children }: { children: ReactNode }) {
  return <ThemeContext.Provider value={useThemeState()}>{children}</ThemeContext.Provider>;
}

/** The shared theme; without a ThemeProvider above, a theme state of the caller's own. */
export function useTheme(): ThemeState {
  const own = useThemeState();
  return useContext(ThemeContext) ?? own;
}

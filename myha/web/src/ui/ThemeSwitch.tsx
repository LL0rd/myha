import { useState } from "react";

const themes = ["light", "dark", "contrast"] as const;

// shortcut: proves the token themes only; no persistence and no way back to the system theme until the ThemeProvider task.
export function ThemeSwitch() {
  const [theme, setTheme] = useState<(typeof themes)[number] | undefined>();
  return (
    <div role="group" aria-label="Theme">
      {themes.map((t) => (
        <button
          key={t}
          type="button"
          aria-pressed={t === theme}
          onClick={() => {
            document.documentElement.dataset.theme = t;
            setTheme(t);
          }}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

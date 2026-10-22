import { themes, useTheme } from "./ThemeProvider";

export function ThemeSwitch() {
  const { theme, setTheme } = useTheme();
  return (
    <div role="group" aria-label="Theme">
      {themes.map((t) => (
        <button key={t} type="button" aria-pressed={t === theme} onClick={() => setTheme(t)}>
          {t}
        </button>
      ))}
    </div>
  );
}

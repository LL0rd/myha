import { useI18n } from "../i18n";
import { themes, useTheme } from "./ThemeProvider";

export function ThemeSwitch() {
  const { theme, setTheme } = useTheme();
  const { t } = useI18n();
  return (
    <div role="group" aria-label={t("theme.label")}>
      {themes.map((name) => (
        <button key={name} type="button" aria-pressed={name === theme} onClick={() => setTheme(name)}>
          {t(`theme.${name}`)}
        </button>
      ))}
    </div>
  );
}

import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { langs, useI18n } from "../i18n";
import { ThemeSwitch } from "../ui/ThemeSwitch";
import styles from "./guest.module.css";
import { Dashboard } from "./routes/Dashboard";
import { Ended } from "./routes/Ended";
import { Join } from "./routes/Join";
import { SessionContext, type JoinFragment, type Session } from "./store/session";

function Preferences() {
  const { t, lang, setLang } = useI18n();
  return (
    <footer className={styles.prefs}>
      <ThemeSwitch />
      <div role="group" aria-label={t("language.label")}>
        {langs.map((l) => (
          <button key={l} type="button" lang={l} aria-pressed={l === lang} onClick={() => setLang(l)}>
            {t(`language.${l}`)}
          </button>
        ))}
      </div>
    </footer>
  );
}

export function App({ session, fragment }: { session: Session; fragment: JoinFragment | null }) {
  return (
    <SessionContext.Provider value={session}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/join" element={<Join fragment={fragment} />} />
          <Route path="/ended" element={<Ended />} />
          <Route path="/" element={<Dashboard />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
      <Preferences />
    </SessionContext.Provider>
  );
}

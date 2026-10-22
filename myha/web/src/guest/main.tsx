import "@fontsource/nunito-sans/400.css";
import "@fontsource/nunito-sans/600.css";
import "@fontsource/nunito-sans/700.css";
import "@fontsource/nunito-sans/800.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "../ui/tokens.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initTheme } from "../ui/ThemeProvider";
import { App } from "./App";
import { createSession, takeJoinFragment } from "./store/session";

// First of all, before React or any request: the invite secret leaves the address bar.
const fragment = location.pathname === "/join" ? takeJoinFragment() : null;

initTheme();

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {}); // the page works without it
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App session={createSession()} fragment={fragment} />
  </StrictMode>,
);

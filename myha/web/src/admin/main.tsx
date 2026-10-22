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
import { ThemeSwitch } from "../ui/ThemeSwitch";

function App() {
  return (
    <main>
      <h1>myHA Admin</h1>
      <ThemeSwitch />
    </main>
  );
}

initTheme(); // before the first render: no flash of the wrong theme

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

import "@fontsource/nunito-sans/400.css";
import "@fontsource/nunito-sans/600.css";
import "@fontsource/nunito-sans/700.css";
import "@fontsource/nunito-sans/800.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "../ui/tokens.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeSwitch } from "../ui/ThemeSwitch";

function App() {
  return (
    <main>
      <h1>myHA</h1>
      <ThemeSwitch />
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

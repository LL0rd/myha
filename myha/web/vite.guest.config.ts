import { defineConfig, mergeConfig } from "vite";
import { shared } from "./vite.shared";

export default mergeConfig(
  shared,
  defineConfig({
    // The public listener always serves guests at the origin root; absolute asset URLs keep
    // deep links such as /stays/42 working. The admin bundle keeps "./" for the ingress prefix.
    base: "/",
    build: { outDir: "dist/guest", emptyOutDir: true, rollupOptions: { input: "index.html" } },
  }),
);

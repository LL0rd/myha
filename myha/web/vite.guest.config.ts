import { defineConfig, mergeConfig } from "vite";
import { shared } from "./vite.shared";

export default mergeConfig(
  shared,
  defineConfig({
    // The public listener always serves guests at the origin root; absolute asset URLs keep
    // deep links such as /stays/42 working. The admin bundle keeps "./" for the ingress prefix.
    base: "/",
    build: {
      outDir: "dist/guest",
      emptyOutDir: true,
      rollupOptions: {
        // The service worker is its own entry at /sw.js: its scope is the whole origin, and its URL must not change.
        input: { index: "index.html", sw: "src/sw.ts" },
        output: { entryFileNames: (chunk) => (chunk.name === "sw" ? "sw.js" : "assets/[name]-[hash].js") },
      },
    },
  }),
);

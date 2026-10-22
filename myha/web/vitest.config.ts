import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Vitest blanks every CSS import unless CSS processing is on for it, ?raw included; tokens.test.ts needs the text.
    css: { include: [/\.css\?raw$/] },
    projects: [
      { extends: true, test: { name: "node", include: ["src/**/*.test.ts"] } },
      { extends: true, test: { name: "dom", include: ["src/**/*.test.tsx"], environment: "jsdom" } },
    ],
  },
});

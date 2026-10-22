import { defineConfig } from "vitest/config";

export default defineConfig({
  // Vitest blanks every CSS import unless CSS processing is on for it, ?raw included; tokens.test.ts needs the text.
  test: { css: { include: [/\.css\?raw$/] } },
});

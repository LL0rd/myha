import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default defineConfig(
  { ignores: ["dist"] },
  js.configs.recommended,
  tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  {
    // Everything under src except src/admin can end up in the guest bundle.
    files: ["src/**"],
    ignores: ["src/admin/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        { patterns: [{ regex: "(^|/)admin(/|$)", message: "Admin code must not reach the guest bundle." }] },
      ],
    },
  },
  {
    // zod probes `new Function` on first use unless schema.ts has set jitless; the CSP would report that probe.
    files: ["src/**"],
    ignores: ["src/api/schema.ts"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          paths: [{ name: "zod", message: "Import zod only in src/api/schema.ts (jitless for the CSP).", allowTypeImports: true }],
          patterns: [{ group: ["zod/*"], message: "Import zod only in src/api/schema.ts.", allowTypeImports: true }],
        },
      ],
    },
  },
);

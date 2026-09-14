import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = defineConfig([
  ...nextVitals,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Not ours to lint: the documentation site's build output and the
    // virtualenv that builds it both ship vendored JS (lunr, wordcut) which
    // otherwise reports errors that have nothing to do with the app.
    "site/**",
    ".venv-docs/**",
    ".venv/**",
  ]),
]);

export default eslintConfig;

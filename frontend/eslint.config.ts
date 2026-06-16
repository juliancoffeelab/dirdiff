import { defineConfig } from "eslint/config";
import type { Rule } from "eslint";
import tseslint from "typescript-eslint";
import showWhenBooleanRule from "./eslint-rules/show-when-boolean.mjs";

const configRootDir = new URL(".", import.meta.url).pathname;
const localRules = {
  "show-when-boolean": showWhenBooleanRule as Rule.RuleModule,
};

export default defineConfig(
  {
    ignores: ["dist/**", "node_modules/**", ".vite/**"],
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: configRootDir,
      },
    },
    plugins: {
      "@typescript-eslint": tseslint.plugin,
      local: {
        rules: localRules,
      },
    },
    rules: {
      "@typescript-eslint/strict-boolean-expressions": [
        "error",
        {
          allowNullableBoolean: false,
          allowNullableString: false,
          allowNullableNumber: false,
          allowNullableObject: false,
          allowNullableEnum: false,
          allowAny: false,
          allowRuleToRunWithoutStrictNullChecksIKnowWhatIAmDoing: false,
        },
      ],
      "local/show-when-boolean": "error",
    },
  },
  {
    files: ["eslint.config.ts", "eslint-rules/**/*.mjs"],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        project: "./tsconfig.tools.json",
        tsconfigRootDir: configRootDir,
      },
    },
    plugins: {
      "@typescript-eslint": tseslint.plugin,
    },
    rules: {
      "@typescript-eslint/no-deprecated": "error",
    },
  },
);

import { defineConfig } from "eslint/config";
import type { Rule } from "eslint";
import tseslint from "typescript-eslint";
import { fileCardFacadeRule } from "./eslint-rules/file-card-facade.mjs";
import { nestedModuleHelperRule } from "./eslint-rules/helper-topology.mjs";
import showWhenBooleanRule from "./eslint-rules/show-when-boolean.mjs";

const configRootDir = new URL(".", import.meta.url).pathname;
const localRules = {
  "file-card-facade": fileCardFacadeRule as Rule.RuleModule,
  "nested-module-helper": nestedModuleHelperRule as Rule.RuleModule,
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
      "@typescript-eslint/consistent-type-assertions": [
        "error",
        { assertionStyle: "never" },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      "local/file-card-facade": "error",
      "local/nested-module-helper": "error",
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

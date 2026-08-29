import { defineConfig } from "eslint/config";
import type { Rule } from "eslint";
import tseslint from "typescript-eslint";
import { dirdiffDocRule } from "./eslint-rules/dirdiff-doc.mjs";
import { fileCardFacadeRule } from "./eslint-rules/file-card-facade.mjs";
import {
  nestedModuleHelperRule,
  selectHunkCallersRule,
} from "./eslint-rules/helper-topology.mjs";
import showWhenBooleanRule from "./eslint-rules/show-when-boolean.mjs";

const configRootDir = new URL(".", import.meta.url).pathname;
const localRules = {
  "dirdiff-doc": dirdiffDocRule as Rule.RuleModule,
  "file-card-facade": fileCardFacadeRule as Rule.RuleModule,
  "nested-module-helper": nestedModuleHelperRule as Rule.RuleModule,
  "select-hunk-callers": selectHunkCallersRule as Rule.RuleModule,
  "show-when-boolean": showWhenBooleanRule as Rule.RuleModule,
};

export default defineConfig(
  {
    ignores: ["dist/**", "node_modules/**", ".vite/**"],
  },
  {
    files: ["src/hud/navigation.tsx"],
    rules: {
      "local/select-hunk-callers": "error",
    },
  },
  {
    files: ["src/**/*.{ts,tsx}", "tests/**/*.{ts,tsx}"],
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
      "local/dirdiff-doc": "error",
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

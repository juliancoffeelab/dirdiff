import tseslint from "typescript-eslint";
import showWhenBooleanRule from "./eslint-rules/show-when-boolean.mjs";

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", ".vite/**"],
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "@typescript-eslint": tseslint.plugin,
      local: {
        rules: {
          "show-when-boolean": showWhenBooleanRule,
        },
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
);

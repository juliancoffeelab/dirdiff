import { defineConfig } from "eslint/config";
import type { Rule } from "eslint";

import baseConfig from "./eslint.config";
import { inlineModuleHelperRule } from "./eslint-rules/helper-topology.mjs";
import noNullishAccessorRule from "./eslint-rules/no-nullish-accessor.mjs";
import noNullishFallbackChainRule from "./eslint-rules/no-nullish-fallback-chain.mjs";
import noOrFallbackChainRule from "./eslint-rules/no-or-fallback-chain.mjs";

const humanRules = {
  "inline-module-helper": inlineModuleHelperRule as Rule.RuleModule,
  "no-nullish-accessor": noNullishAccessorRule as Rule.RuleModule,
  "no-nullish-fallback-chain": noNullishFallbackChainRule as Rule.RuleModule,
  "no-or-fallback-chain": noOrFallbackChainRule as Rule.RuleModule,
};

export default defineConfig(...baseConfig, {
  files: ["src/**/*.{ts,tsx}"],
  plugins: {
    human: {
      rules: humanRules,
    },
  },
  rules: {
    "human/inline-module-helper": "error",
    "human/no-nullish-accessor": "error",
    "human/no-nullish-fallback-chain": "error",
    "human/no-or-fallback-chain": "error",
  },
});

// @ts-nocheck

function isOptionalAccess(node) {
  if (node.type === "MemberExpression" || node.type === "CallExpression") {
    return node.optional === true;
  }

  return false;
}

const noNullishAccessorRule = {
  meta: {
    type: "problem",
    docs: {
      description: "Disallow optional chaining access.",
    },
    schema: [],
    messages: {
      nullishAccessor:
        "Avoid optional chaining access; make the nullish case explicit.",
    },
  },
  create(context) {
    return {
      "MemberExpression, CallExpression"(node) {
        if (!isOptionalAccess(node)) {
          return;
        }

        context.report({
          node,
          messageId: "nullishAccessor",
        });
      },
    };
  },
};

export default noNullishAccessorRule;

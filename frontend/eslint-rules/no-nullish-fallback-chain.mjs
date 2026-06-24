// @ts-nocheck

function isNullishFallbackChain(node) {
  return (
    node.type === "LogicalExpression" &&
    node.operator === "??" &&
    node.left.type === "LogicalExpression" &&
    node.left.operator === "??"
  );
}

function isNestedInNullishFallbackChain(node) {
  return (
    node.parent.type === "LogicalExpression" && node.parent.operator === "??"
  );
}

const noNullishFallbackChainRule = {
  meta: {
    type: "problem",
    docs: {
      description: "Disallow chained nullish coalescing fallbacks.",
    },
    schema: [],
    messages: {
      nullishFallbackChain:
        "Avoid chained '??' fallbacks; make the fallback logic explicit.",
    },
  },
  create(context) {
    return {
      LogicalExpression(node) {
        if (!isNullishFallbackChain(node)) {
          return;
        }
        if (isNestedInNullishFallbackChain(node)) {
          return;
        }

        context.report({
          node,
          messageId: "nullishFallbackChain",
        });
      },
    };
  },
};

export default noNullishFallbackChainRule;

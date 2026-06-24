// @ts-nocheck

function isOrFallbackChain(node) {
  return (
    node.type === "LogicalExpression" &&
    node.operator === "||" &&
    node.left.type === "LogicalExpression" &&
    node.left.operator === "||"
  );
}

function isNestedInOrFallbackChain(node) {
  return (
    node.parent.type === "LogicalExpression" && node.parent.operator === "||"
  );
}

const noOrFallbackChainRule = {
  meta: {
    type: "problem",
    docs: {
      description: "Disallow chained logical-or fallbacks.",
    },
    schema: [],
    messages: {
      orFallbackChain:
        "Avoid chained '||' fallbacks; make the fallback logic explicit.",
    },
  },
  create(context) {
    return {
      LogicalExpression(node) {
        if (!isOrFallbackChain(node)) {
          return;
        }
        if (isNestedInOrFallbackChain(node)) {
          return;
        }

        context.report({
          node,
          messageId: "orFallbackChain",
        });
      },
    };
  },
};

export default noOrFallbackChainRule;

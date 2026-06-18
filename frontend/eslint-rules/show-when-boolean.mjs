// @ts-nocheck

import ts from "typescript";
function isBooleanType(type, checker) {
  const normalized = checker.getBaseTypeOfLiteralType(type);
  if ((normalized.flags & ts.TypeFlags.BooleanLike) !== 0) {
    return true;
  }
  if (type.isUnion()) {
    return type.types.every((part) => isBooleanType(part, checker));
  }
  return false;
}

function isShowElement(node) {
  return node.name.type === "JSXIdentifier" && node.name.name === "Show";
}

function isWhenAttribute(attribute) {
  return (
    attribute.type === "JSXAttribute" &&
    attribute.name.type === "JSXIdentifier" &&
    attribute.name.name === "when"
  );
}

function hasFunctionChild(node) {
  return node.children.some(
    (child) =>
      child.type === "JSXExpressionContainer" &&
      (child.expression.type === "ArrowFunctionExpression" ||
        child.expression.type === "FunctionExpression"),
  );
}

const showWhenBooleanRule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Require Solid <Show when={...}> conditions to be boolean-typed.",
    },
    schema: [],
    messages: {
      nonBooleanWhen:
        "<Show when={...}> must receive a boolean expression, not a truthy value of type '{{type}}'.",
    },
  },
  create(context) {
    const services = context.sourceCode.parserServices;
    if (
      services === null ||
      services.program === null ||
      services.esTreeNodeToTSNodeMap === null
    ) {
      return {};
    }

    const checker = services.program.getTypeChecker();

    return {
      JSXOpeningElement(node) {
        if (!isShowElement(node)) {
          return;
        }

        const whenAttribute = node.attributes.find(isWhenAttribute);
        if (whenAttribute === undefined) {
          return;
        }
        if (
          whenAttribute.value === null ||
          whenAttribute.value.type !== "JSXExpressionContainer" ||
          whenAttribute.value.expression.type === "JSXEmptyExpression"
        ) {
          return;
        }

        const expression = whenAttribute.value.expression;
        const tsNode = services.esTreeNodeToTSNodeMap.get(expression);
        const type = checker.getTypeAtLocation(tsNode);

        if (isBooleanType(type, checker) || hasFunctionChild(node.parent)) {
          return;
        }

        context.report({
          node: expression,
          messageId: "nonBooleanWhen",
          data: {
            type: checker.typeToString(type),
          },
        });
      },
    };
  },
};

export default showWhenBooleanRule;

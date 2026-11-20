// @ts-nocheck

function helperTopologyRule(diagnosticKind) {
  return {
    meta: {
      type: "suggestion",
      docs: {
        description:
          diagnosticKind === "inline"
            ? "Report short module-local functions with one external reference."
            : "Report short module-local functions referenced beneath one function.",
      },
      schema: [],
      messages: {
        inline:
          "Module-local function '{{name}}' has one external reference; inline it or nest it beside that use.",
        nest: "Module-local function '{{name}}' is referenced only beneath one function; nest it there.",
        colocate:
          "Type guard '{{name}}' must remain directly after its module-local type declaration.",
      },
    },

    create(context) {
      const sourceCode = context.sourceCode;

      return {
        "Program:exit"(program) {
          const moduleScope = sourceCode.scopeManager.scopes.find(
            (scope) => scope.type === "module" && scope.block === program,
          );
          if (moduleScope === undefined) {
            return;
          }

          // Export syntax is the module's explicit public-interface boundary.
          const publicNames = new Set();
          for (const statement of program.body) {
            if (statement.type === "ExportNamedDeclaration") {
              const declaration = statement.declaration;
              if (
                declaration !== null &&
                declaration.type === "FunctionDeclaration" &&
                declaration.id !== null
              ) {
                publicNames.add(declaration.id.name);
              } else if (
                declaration !== null &&
                declaration.type === "VariableDeclaration"
              ) {
                for (const declarator of declaration.declarations) {
                  if (declarator.id.type === "Identifier") {
                    publicNames.add(declarator.id.name);
                  }
                }
              }

              if (statement.source === null) {
                for (const specifier of statement.specifiers) {
                  if (
                    specifier.type === "ExportSpecifier" &&
                    specifier.local.type === "Identifier"
                  ) {
                    publicNames.add(specifier.local.name);
                  }
                }
              }
            } else if (statement.type === "ExportDefaultDeclaration") {
              const declaration = statement.declaration;
              if (declaration.type === "Identifier") {
                publicNames.add(declaration.name);
              } else if (
                declaration.type === "FunctionDeclaration" &&
                declaration.id !== null
              ) {
                publicNames.add(declaration.id.name);
              }
            } else if (
              statement.type === "TSExportAssignment" &&
              statement.expression.type === "Identifier"
            ) {
              publicNames.add(statement.expression.name);
            }
          }

          function outermostFunction(identifier) {
            let container = null;
            let child = identifier;
            let node = identifier.parent;
            while (node.type !== "Program") {
              if (
                (node.type === "ArrowFunctionExpression" ||
                  node.type === "FunctionExpression" ||
                  node.type === "FunctionDeclaration") &&
                child === node.body
              ) {
                container = node;
              }
              child = node;
              node = node.parent;
            }
            return container;
          }

          function guardedTypeName(functionNode, functionName) {
            if (!functionName.startsWith("is")) {
              return null;
            }
            const predicate = functionNode.returnType?.typeAnnotation;
            const guardedType = predicate?.typeAnnotation?.typeAnnotation;
            if (
              predicate?.type !== "TSTypePredicate" ||
              guardedType?.type !== "TSTypeReference" ||
              guardedType.typeName.type !== "Identifier"
            ) {
              return null;
            }

            return guardedType.typeName.name;
          }

          function followsType(functionNode, guardedTypeName) {
            let statement = functionNode;
            if (functionNode.parent.type === "VariableDeclarator") {
              statement = functionNode.parent.parent;
            }
            const statementIndex = program.body.indexOf(statement);
            if (statementIndex < 1) {
              return false;
            }
            let preceding = program.body[statementIndex - 1];
            if (
              preceding.type === "ExportNamedDeclaration" &&
              preceding.declaration !== null
            ) {
              preceding = preceding.declaration;
            }
            return (
              (preceding.type === "TSTypeAliasDeclaration" ||
                preceding.type === "TSInterfaceDeclaration") &&
              preceding.id.name === guardedTypeName
            );
          }

          const localTypeNames = new Set();
          for (const statement of program.body) {
            const declaration =
              statement.type === "ExportNamedDeclaration"
                ? statement.declaration
                : statement;
            if (
              declaration?.type === "TSTypeAliasDeclaration" ||
              declaration?.type === "TSInterfaceDeclaration"
            ) {
              localTypeNames.add(declaration.id.name);
            }
          }

          for (const variable of moduleScope.variables) {
            if (
              publicNames.has(variable.name) ||
              /^[A-Z](?:[a-z0-9]+(?:[A-Z][a-z0-9]*)*)?$/u.test(variable.name) ||
              variable.name.startsWith("test_") ||
              variable.name === "main"
            ) {
              continue;
            }

            // Only a unique, direct module declaration has unambiguous topology.
            let functionNode = null;
            if (variable.defs.length === 1) {
              const definition = variable.defs[0];
              if (
                definition.type === "FunctionName" &&
                definition.node.type === "FunctionDeclaration" &&
                definition.node.body !== null &&
                definition.node.parent.type === "Program"
              ) {
                functionNode = definition.node;
              } else if (
                definition.type === "Variable" &&
                definition.node.type === "VariableDeclarator" &&
                definition.node.parent.type === "VariableDeclaration" &&
                definition.node.parent.parent.type === "Program"
              ) {
                const initializer = definition.node.init;
                if (
                  initializer !== null &&
                  (initializer.type === "ArrowFunctionExpression" ||
                    initializer.type === "FunctionExpression")
                ) {
                  functionNode = initializer;
                }
              }
            }
            if (functionNode === null) {
              continue;
            }
            const guardedType = guardedTypeName(functionNode, variable.name);
            if (guardedType !== null && localTypeNames.has(guardedType)) {
              if (!followsType(functionNode, guardedType)) {
                context.report({
                  node: variable.identifiers[0],
                  messageId: "colocate",
                  data: { name: variable.name },
                });
              }
              continue;
            }
            if (
              functionNode.loc.end.line - functionNode.loc.start.line + 1 >
              10
            ) {
              continue;
            }
            if (
              Array.isArray(functionNode.decorators) &&
              functionNode.decorators.length > 0
            ) {
              continue;
            }

            const references = variable.references.filter((reference) => {
              if (!reference.isRead()) {
                return false;
              }

              // Self-references and nested closures are internal to the binding.
              let node = reference.identifier.parent;
              while (node.type !== "Program") {
                if (node === functionNode) {
                  return false;
                }
                node = node.parent;
              }
              return true;
            });
            if (references.length === 1) {
              if (diagnosticKind === "inline") {
                context.report({
                  node: variable.identifiers[0],
                  messageId: "inline",
                  data: { name: variable.name },
                });
              }
              continue;
            }
            if (references.length < 2 || diagnosticKind !== "nest") {
              continue;
            }

            const container = outermostFunction(references[0].identifier);
            if (
              container !== null &&
              references.every(
                (reference) =>
                  outermostFunction(reference.identifier) === container,
              )
            ) {
              context.report({
                node: variable.identifiers[0],
                messageId: "nest",
                data: { name: variable.name },
              });
            }
          }
        },
      };
    },
  };
}

export const inlineModuleHelperRule = helperTopologyRule("inline");
export const nestedModuleHelperRule = helperTopologyRule("nest");

export const selectHunkCallersRule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Require exactly the five approved direct selectHunk callers.",
    },
    schema: [],
    messages: {
      declaration:
        "navigation.tsx must declare one module-local selectHunk function.",
      indirect:
        "selectHunk may only appear as the callee of an approved direct call.",
      caller: "selectHunk has unapproved direct caller '{{caller}}'.",
      count:
        "Approved selectHunk caller '{{caller}}' must contain exactly one direct call; found {{count}}.",
    },
  },

  create(context) {
    const sourceCode = context.sourceCode;
    const approvedCallers = [
      "writeInitialHunkSelection",
      "scrollFollow",
      "navigateToFile",
      "nextHunk",
      "prevHunk",
    ];
    const approvedCallerSet = new Set(approvedCallers);

    return {
      "Program:exit"(program) {
        const moduleScope = sourceCode.scopeManager.scopes.find(
          (scope) => scope.type === "module" && scope.block === program,
        );
        const selection = moduleScope?.variables.find(
          (variable) => variable.name === "selectHunk",
        );
        if (
          selection === undefined ||
          selection.defs.length !== 1 ||
          selection.defs[0].type !== "FunctionName" ||
          selection.defs[0].node.type !== "FunctionDeclaration" ||
          selection.defs[0].node.parent.type !== "Program"
        ) {
          context.report({ node: program, messageId: "declaration" });
          return;
        }

        function directCallerName(call) {
          let node = call.parent;
          while (node.type !== "Program") {
            if (
              node.type === "ArrowFunctionExpression" ||
              node.type === "FunctionExpression"
            ) {
              return null;
            }
            if (node.type === "FunctionDeclaration") {
              return node.id?.name ?? null;
            }
            node = node.parent;
          }
          return null;
        }

        const counts = new Map(approvedCallers.map((caller) => [caller, 0]));
        for (const reference of selection.references) {
          if (!reference.isRead()) {
            continue;
          }
          const identifier = reference.identifier;
          const parent = identifier.parent;
          if (
            parent.type !== "CallExpression" ||
            parent.callee !== identifier
          ) {
            context.report({ node: identifier, messageId: "indirect" });
            continue;
          }
          const caller = directCallerName(parent);
          if (caller === null || !approvedCallerSet.has(caller)) {
            context.report({
              node: identifier,
              messageId: "caller",
              data: { caller: caller ?? "anonymous or module-level code" },
            });
            continue;
          }
          counts.set(caller, (counts.get(caller) ?? 0) + 1);
        }
        for (const caller of approvedCallers) {
          const count = counts.get(caller) ?? 0;
          if (count !== 1) {
            context.report({
              node: program,
              messageId: "count",
              data: { caller, count: String(count) },
            });
          }
        }
      },
    };
  },
};

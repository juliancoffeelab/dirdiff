// @ts-nocheck

/**
 * Report module-local functions whose reference topology calls for inlining or
 * lexical nesting.
 *
 * The module exports separate ESLint rules for inline and nesting diagnostics.
 * They examine only bindings declared as top-level function declarations or
 * top-level variables initialized with a function. Exported bindings are public
 * interfaces and are outside the rules. PascalCase bindings, `test_*`, `main`,
 * decorated functions, functions whose inclusive source span exceeds ten
 * lines, and type guards placed directly after their module-local type are also
 * outside the rules. For each remaining binding, the rules count resolved read
 * references, excluding references from inside the function itself. One
 * reference produces an inline diagnostic; several references beneath the same
 * outermost function produce a nesting diagnostic.
 *
 * The rule owns only per-file analysis state created by ESLint. It does not
 * estimate function size, infer whether a separate semantic contract is useful,
 * alter source, or inspect references outside the current module.
 */
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

    /**
     * Analyze one parsed module after ESLint has resolved its lexical bindings.
     *
     * @param {import("eslint").Rule.RuleContext} context ESLint's per-file rule
     * context.
     * @returns {import("eslint").Rule.RuleListener} Visitors that report helper
     * topology after the complete module has been parsed.
     */
    create(context) {
      const sourceCode = context.sourceCode;

      return {
        /**
         * Report candidates once scope construction and reference resolution are
         * complete.
         *
         * @param {import("estree").Program} program Parsed module root.
         */
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

          /**
           * Find the outermost function containing a resolved reference.
           *
           * References in callbacks nested under a component belong to the
           * component, which is the scope into which a shared helper can move.
           *
           * @param {import("estree").Identifier} identifier Resolved reference.
           * @returns {import("estree").Function | null} Outermost containing
           * function, or null for module-level syntax.
           */
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

          /**
           * Read the simple type named by a genuine `is*` type predicate.
           *
           * @param {import("estree").Function} functionNode Candidate function.
           * @param {string} functionName Candidate binding name.
           * @returns {string | null} Guarded type name, or null when the
           * function is not a supported type guard.
           */
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

          /**
           * Report whether a function immediately follows the named type.
           *
           * @param {import("estree").Function} functionNode Type guard.
           * @param {string} guardedTypeName Module-local guarded type name.
           * @returns {boolean} Whether the declarations are adjacent.
           */
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

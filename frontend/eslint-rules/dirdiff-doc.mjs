// @ts-nocheck

/**
 * Enforce dirdiff's TypeScript documentation placement rules.
 *
 * The rule checks source modules, module-level runtime values, declared types
 * and callables, callable parameters and section order, fields and enum
 * variants, and Solid `createEffect` calls.
 * Declarations use adjacent JSDoc so editors can present their contracts.
 * Effects accept an adjacent line or block comment because their documentation
 * describes a local reactive operation. Every diagnostic links to the
 * corresponding section of the documentation guide.
 *
 * The rule inspects syntax and comment placement only. It does not judge prose,
 * infer aliases of `createEffect`, or require documentation on inline callback
 * expressions that do not declare a named callable.
 */

const guides = {
  module: "docs/how_to_docs.md#module-docstrings",
  type: "docs/how_to_docs.md#type-docstrings",
  callable: "docs/how_to_docs.md#function-and-method-docstrings",
  global: "docs/how_to_docs.md#global-value-documentation",
  effect: "docs/how_to_docs.md#other-javascript-and-typescript-documentation",
};

/** @type {import("eslint").Rule.RuleModule} */
export const dirdiffDocRule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Require dirdiff documentation on TypeScript declarations and effects.",
    },
    schema: [],
    messages: {
      missingModule: "Module needs documentation; see {{guide}}.",
      missingType: "Type '{{name}}' needs documentation; see {{guide}}.",
      missingCallable:
        "Callable '{{name}}' needs documentation; see {{guide}}.",
      missingGlobal:
        "Global value '{{name}}' needs adjacent documentation; see {{guide}}.",
      missingField:
        "Field '{{name}}' needs adjacent documentation; see {{guide}}.",
      typeFieldBullets:
        "Type '{{name}}' puts field documentation in bullet points; move those docs beside the fields; see {{guide}}.",
      typeFieldBlob:
        "Type '{{name}}' mentions {{count}} of its own fields in prose; move individual field descriptions beside their declarations or state one explicit cross-field invariant; see {{guide}}.",
      callableParameters:
        "Callable '{{name}}' needs one @param tag per parameter ({{problems}}); see {{guide}}.",
      callableSectionOrder:
        "Callable '{{name}}' has sections out of order; put parameters first, then # Usage, # Returns, and # Failures when present; see {{guide}}.",
      optionalReturn:
        "Callable '{{name}}' returns {{absence}}; add separate # Returns bullets for the present value and exactly what each absent value means; see {{guide}}.",
      structuredReturn:
        "Callable '{{name}}' returns structured type '{{type}}'; add at least two # Returns bullets dividing that shape into its caller-visible parts; see {{guide}}.",
      missingEffect:
        "createEffect needs an adjacent comment explaining its reactive purpose, inputs, lifetime, and cleanup; see {{guide}}.",
    },
  },

  /**
   * Build visitors that inspect declarations and effect calls in one module.
   *
   * @param {import("eslint").Rule.RuleContext} context ESLint's per-file rule
   * context.
   */
  create(context) {
    const sourceCode = context.sourceCode;

    /**
     * Return the syntax node before which declaration documentation is written.
     *
     * Exported declarations receive comments on their `export` wrapper rather
     * than on the wrapped declaration.
     *
     * @param {import("estree").Node} node Declared syntax node.
     */
    function commentTarget(node) {
      const parent =
        /** @type {import("estree").Node & { declaration?: unknown }} */ (
          node.parent
        );
      if (
        (parent.type === "ExportNamedDeclaration" ||
          parent.type === "ExportDefaultDeclaration") &&
        parent.declaration === node
      ) {
        return parent;
      }
      return node;
    }

    /**
     * Return the adjacent JSDoc attached to a declaration.
     *
     * @param {import("estree").Node} node Declared syntax node.
     * # Returns
     *
     * - `Comment`: the adjacent leading JSDoc.
     * - `null`: the declaration has no adjacent leading JSDoc.
     */
    function jsdocComment(node) {
      const target = commentTarget(node);
      const comments = sourceCode.getCommentsBefore(target);
      const previous = comments.at(-1);
      if (
        previous !== undefined &&
        previous.type === "Block" &&
        previous.value.startsWith("*") &&
        previous.loc !== undefined &&
        target.loc !== undefined &&
        previous.loc.end.line === target.loc.start.line - 1
      ) {
        return previous;
      }
      return null;
    }

    /**
     * Test whether a declaration has adjacent JSDoc.
     *
     * @param {import("estree").Node} node Declared syntax node.
     */
    function hasJsdoc(node) {
      return jsdocComment(node) !== null;
    }

    /**
     * Report missing declaration documentation.
     *
     * @param {import("estree").Node} node Declared syntax node.
     * @param {"missingType" | "missingCallable" | "missingField" | "missingGlobal"} messageId Diagnostic kind.
     * @param {string} name User-facing declaration name.
     * @param {string} guide Documentation-guide link.
     */
    function requireJsdoc(node, messageId, name, guide) {
      if (hasJsdoc(node)) {
        return;
      }
      context.report({ node, messageId, data: { name, guide } });
    }

    /**
     * Reject field details stored as bullets in a type-level doc block.
     *
     * @param {import("estree").Node} node Declared type node.
     * @param {string} name User-facing type name.
     * @param {boolean} hasFields Whether the type declares fields or variants.
     */
    function rejectFieldBullets(node, name, hasFields) {
      const comment = jsdocComment(node);
      if (!hasFields || comment === null) {
        return;
      }
      const containsBullet = comment.value.split("\n").some((line) => {
        const content = line.replace(/^\s*\*\s?/, "");
        return /^\s*(?:[-*+]|\d+[.)])\s+/.test(content);
      });
      if (containsBullet) {
        context.report({
          node,
          messageId: "typeFieldBullets",
          data: { name, guide: guides.type },
        });
      }
    }

    /**
     * Report type prose that likely enumerates fields as a paragraph.
     *
     * Three or more backticked field names indicate that individual field docs
     * were placed on the type. A real cross-field invariant can avoid the
     * diagnostic by stating one relationship instead of enumerating the type.
     *
     * @param {import("estree").Node} node Declared type node.
     * @param {string} name User-facing type name.
     * @param {readonly string[]} fieldNames Direct field or variant names.
     */
    function rejectFieldBlob(node, name, fieldNames) {
      const comment = jsdocComment(node);
      if (comment === null) {
        return;
      }
      const ownFields = new Set(fieldNames);
      const mentioned = new Set();
      for (const match of comment.value.matchAll(/`([A-Za-z_$][\w$]*)`/g)) {
        const fieldName = match[1];
        if (ownFields.has(fieldName)) {
          mentioned.add(fieldName);
        }
      }
      if (mentioned.size < 3) {
        return;
      }
      context.report({
        node,
        messageId: "typeFieldBlob",
        data: { name, count: String(mentioned.size), guide: guides.type },
      });
    }

    /**
     * Read the identifier exposed by one callable parameter.
     *
     * Destructured parameters have no identifier suitable for an exact
     * `@param` tag and remain outside this syntax check.
     *
     * @param {import("estree").Node} parameter Callable parameter node.
     * # Returns
     *
     * - `string`: the declared parameter name.
     * - `null`: the parameter syntax does not declare one stable name.
     */
    function parameterName(parameter) {
      if (parameter.type === "Identifier") {
        return parameter.name === "this" ? null : parameter.name;
      }
      if (parameter.type === "AssignmentPattern") {
        return parameterName(parameter.left);
      }
      if (parameter.type === "RestElement") {
        return parameterName(parameter.argument);
      }
      if (parameter.type === "TSParameterProperty") {
        return parameterName(parameter.parameter);
      }
      return null;
    }

    /**
     * Require exact `@param` tags for a callable with several named inputs.
     *
     * @param {import("estree").Node} documentationNode Declaration carrying
     * JSDoc.
     * @param {import("estree").Node} reportNode Syntax node reported by ESLint.
     * @param {string} name User-facing callable name.
     * @param {readonly import("estree").Node[]} parameters Declared parameters.
     */
    function requireParameterTags(
      documentationNode,
      reportNode,
      name,
      parameters,
    ) {
      const comment = jsdocComment(documentationNode);
      if (comment === null) {
        return;
      }

      const headings = [];
      const parameterLines = [];
      let fenceMarker = null;
      const lines = comment.value.split("\n");
      for (const [lineNumber, line] of lines.entries()) {
        const content = line.replace(/^\s*\*\s?/, "").trim();
        const fenceMatch = /^(`{3,}|~{3,})/.exec(content);
        if (fenceMarker === null && fenceMatch !== null) {
          fenceMarker = fenceMatch[1];
          continue;
        }
        if (fenceMarker !== null) {
          const fenceCharacter = fenceMarker[0];
          if (
            new RegExp(`^${fenceCharacter}{${fenceMarker.length},}\\s*$`).test(
              content,
            )
          ) {
            fenceMarker = null;
          }
          continue;
        }
        if (content.startsWith("@param ")) {
          parameterLines.push(lineNumber);
        }
        if (content.startsWith("# ")) {
          headings.push([lineNumber, content]);
        }
      }

      parameterLines.push(
        ...headings
          .filter(([, heading]) => heading === "# Parameters")
          .map(([lineNumber]) => lineNumber),
      );
      const usagePositions = headings
        .filter(([, heading]) => heading.startsWith("# Usage"))
        .map(([lineNumber]) => lineNumber);
      const returnPositions = headings
        .filter(([, heading]) => heading === "# Returns")
        .map(([lineNumber]) => lineNumber);
      const failurePositions = headings
        .filter(([, heading]) => heading === "# Failures")
        .map(([lineNumber]) => lineNumber);
      const nonParameterPositions = headings
        .filter(([, heading]) => heading !== "# Parameters")
        .map(([lineNumber]) => lineNumber);
      const parametersAreFirst =
        parameterLines.length === 0 ||
        nonParameterPositions.length === 0 ||
        Math.max(...parameterLines) < Math.min(...nonParameterPositions);
      const usagePrecedesReturns =
        usagePositions.length === 0 ||
        returnPositions.length === 0 ||
        Math.max(...usagePositions) < Math.min(...returnPositions);
      const usagePrecedesFailures =
        usagePositions.length === 0 ||
        failurePositions.length === 0 ||
        Math.max(...usagePositions) < Math.min(...failurePositions);
      const returnsPrecedeFailures =
        returnPositions.length === 0 ||
        failurePositions.length === 0 ||
        Math.max(...returnPositions) < Math.min(...failurePositions);
      if (
        !parametersAreFirst ||
        !usagePrecedesReturns ||
        !usagePrecedesFailures ||
        !returnsPrecedeFailures
      ) {
        context.report({
          node: reportNode,
          messageId: "callableSectionOrder",
          data: { name, guide: guides.callable },
        });
      }

      const expectedNames = parameters
        .map(parameterName)
        .filter((parameter) => parameter !== null);
      if (expectedNames.length < 2) {
        return;
      }

      const documentedNames = [];
      for (const line of comment.value.split("\n")) {
        const match = line.match(
          /^\s*\*\s*@param\s+(?:\{[^}]*\}\s*)?(\[[^\]]+\]|\.\.\.[A-Za-z_$][\w$]*|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)/,
        );
        if (match === null) {
          continue;
        }
        let tagName = match[1];
        if (tagName.startsWith("[") && tagName.endsWith("]")) {
          tagName = tagName.slice(1, -1).split("=", 1)[0];
        }
        tagName = tagName.replace(/^\.\.\./, "");
        if (tagName.includes(".")) {
          continue;
        }
        documentedNames.push(tagName);
      }

      const expected = new Set(expectedNames);
      const actual = new Set(documentedNames);
      const missing = [...expected].filter(
        (parameter) => !actual.has(parameter),
      );
      const unknown = [...actual].filter(
        (parameter) => !expected.has(parameter),
      );
      const duplicates = [...actual].filter(
        (parameter) =>
          documentedNames.filter((documented) => documented === parameter)
            .length > 1,
      );
      if (
        missing.length === 0 &&
        unknown.length === 0 &&
        duplicates.length === 0
      ) {
        return;
      }
      const problems = [];
      if (missing.length > 0) {
        problems.push(`missing ${missing.sort().join(", ")}`);
      }
      if (unknown.length > 0) {
        problems.push(`unknown ${unknown.sort().join(", ")}`);
      }
      if (duplicates.length > 0) {
        problems.push(`duplicate ${duplicates.sort().join(", ")}`);
      }
      context.report({
        node: reportNode,
        messageId: "callableParameters",
        data: { name, problems: problems.join("; "), guide: guides.callable },
      });
    }

    /**
     * Read the unqualified name of one referenced TypeScript type.
     *
     * Qualified references remain atomic but cannot select transparent built-in
     * wrappers such as `Promise` or `Array`.
     *
     * @param {import("estree").Node} annotation Candidate type reference.
     * # Returns
     *
     * - `string`: the unqualified identifier text.
     * - `null`: the annotation has another type shape.
     */
    function typeReferenceName(annotation) {
      if (
        annotation.type === "TSTypeReference" &&
        annotation.typeName.type === "Identifier"
      ) {
        return annotation.typeName.name;
      }
      return null;
    }

    /**
     * Classify an explicit return annotation that needs dedicated prose.
     *
     * A named type is atomic. Arrays and readonly arrays remain atomic only
     * when their item type is atomic. `Promise` is transparent because its type
     * argument is the value an async caller receives.
     *
     * @param {import("estree").Node | undefined} annotation Explicit return type.
     * # Returns
     *
     * - `optional`: the result has top-level absent variants named by `absent`.
     * - `structured`: the result shape needs a caller-facing explanation.
     * - `null`: the result is atomic or a list of atomic values.
     */
    function returnContractKind(annotation) {
      if (annotation === undefined) {
        return null;
      }

      if (
        annotation.type === "TSParenthesizedType" ||
        (annotation.type === "TSTypeOperator" &&
          annotation.operator === "readonly")
      ) {
        return returnContractKind(annotation.typeAnnotation);
      }

      const referenceName = typeReferenceName(annotation);
      const typeArguments =
        annotation.type === "TSTypeReference"
          ? (annotation.typeArguments?.params ?? [])
          : [];
      if (referenceName === "Promise" && typeArguments.length === 1) {
        return returnContractKind(typeArguments[0]);
      }

      if (annotation.type === "TSUnionType") {
        const absent = [];
        if (annotation.types.some((part) => part.type === "TSNullKeyword")) {
          absent.push("null");
        }
        if (
          annotation.types.some((part) => part.type === "TSUndefinedKeyword")
        ) {
          absent.push("undefined");
        }
        return absent.length > 0
          ? { kind: "optional", absent }
          : { kind: "structured" };
      }

      const atomicKinds = new Set([
        "TSAnyKeyword",
        "TSBigIntKeyword",
        "TSBooleanKeyword",
        "TSIntrinsicKeyword",
        "TSNeverKeyword",
        "TSNullKeyword",
        "TSNumberKeyword",
        "TSObjectKeyword",
        "TSStringKeyword",
        "TSSymbolKeyword",
        "TSThisType",
        "TSUndefinedKeyword",
        "TSUnknownKeyword",
        "TSVoidKeyword",
      ]);
      if (
        atomicKinds.has(annotation.type) ||
        annotation.type === "TSLiteralType" ||
        annotation.type === "TSTypePredicate"
      ) {
        return null;
      }
      if (annotation.type === "TSTypeReference") {
        if (
          (referenceName === "Array" || referenceName === "ReadonlyArray") &&
          typeArguments.length === 1
        ) {
          return returnContractKind(typeArguments[0]) === null
            ? null
            : { kind: "structured" };
        }
        return typeArguments.length === 0 ? null : { kind: "structured" };
      }
      if (annotation.type === "TSArrayType") {
        return returnContractKind(annotation.elementType) === null
          ? null
          : { kind: "structured" };
      }
      return { kind: "structured" };
    }

    /**
     * Require return prose for optional and structured result annotations.
     *
     * @param {import("estree").Node} documentationNode Declaration carrying JSDoc.
     * @param {import("estree").Node} reportNode Syntax node reported by ESLint.
     * @param {string} name User-facing callable name.
     * @param {import("estree").Node | undefined} annotation Explicit return type.
     */
    function requireReturnSection(
      documentationNode,
      reportNode,
      name,
      annotation,
    ) {
      const contract = returnContractKind(annotation);
      if (contract === null) {
        return;
      }
      const comment = jsdocComment(documentationNode);
      if (comment === null) {
        return;
      }

      const bullets = [];
      let readingReturn = false;
      for (const line of comment.value.split("\n")) {
        const content = line.replace(/^\s*\*\s?/, "").trim();
        if (content === "# Returns") {
          readingReturn = true;
          continue;
        }
        if (readingReturn && content.startsWith("# ")) {
          break;
        }
        const bullet = content.match(/^-\s+(\S.*)$/);
        if (readingReturn && bullet !== null) {
          bullets.push(bullet[1]);
        }
      }

      if (contract.kind === "optional") {
        const missing = contract.absent.filter(
          (absent) =>
            !bullets.some((bullet) => {
              const normalized = bullet.replaceAll("`", "").trim();
              const match = new RegExp(`^${absent}\\b(.*)$`).exec(normalized);
              return (
                match !== null &&
                match[1].replace(/^[\s*:;,.-]+/, "").trim() !== ""
              );
            }),
        );
        const hasPresentBullet = bullets.some((bullet) => {
          const normalized = bullet.replaceAll("`", "").trim();
          return !contract.absent.some((absent) =>
            new RegExp(`^${absent}\\b`).test(normalized),
          );
        });
        if (
          bullets.length >= contract.absent.length + 1 &&
          missing.length === 0 &&
          hasPresentBullet
        ) {
          return;
        }
        const absence = contract.absent
          .map((part) => `\`${part}\``)
          .join(" or ");
        context.report({
          node: reportNode,
          messageId: "optionalReturn",
          data: { name, absence, guide: guides.callable },
        });
        return;
      }
      if (bullets.length < 2) {
        context.report({
          node: reportNode,
          messageId: "structuredReturn",
          data: {
            name,
            type: sourceCode.getText(annotation),
            guide: guides.callable,
          },
        });
      }
    }

    /**
     * Read a stable display name from property-like syntax.
     *
     * @param {import("estree").Node & { key?: unknown }} node Property node.
     */
    function propertyName(node) {
      const key = /** @type {import("estree").Node | undefined} */ (node.key);
      if (key === undefined) {
        return "<unknown>";
      }
      if (key.type === "Identifier" || key.type === "PrivateIdentifier") {
        return key.name;
      }
      if (key.type === "Literal") {
        return String(key.value);
      }
      return "<computed>";
    }

    /**
     * Test whether an effect call has a directly adjacent local comment.
     *
     * @param {import("estree").CallExpression} node `createEffect` call.
     */
    function hasEffectComment(node) {
      const target =
        node.parent.type === "ExpressionStatement" ? node.parent : node;
      const comment = sourceCode.getCommentsBefore(target).at(-1);
      return (
        comment !== undefined &&
        comment.loc !== undefined &&
        target.loc !== undefined &&
        comment.loc.end.line === target.loc.start.line - 1
      );
    }

    /**
     * Test whether the first statement consumes adjacent JSDoc as its own docs.
     *
     * @param {import("estree").Node} node Module statement.
     */
    function declarationNeedsJsdoc(node) {
      if (
        node.type === "ExportNamedDeclaration" ||
        node.type === "ExportDefaultDeclaration"
      ) {
        return (
          node.declaration !== null && declarationNeedsJsdoc(node.declaration)
        );
      }
      if (
        node.type === "FunctionDeclaration" ||
        node.type === "ClassDeclaration" ||
        node.type === "TSInterfaceDeclaration" ||
        node.type === "TSTypeAliasDeclaration" ||
        node.type === "TSEnumDeclaration"
      ) {
        return true;
      }
      return node.type === "VariableDeclaration";
    }

    return {
      /** Require one leading JSDoc block for the source module. */
      Program(node) {
        const first = node.body[0];
        const comments = sourceCode.getAllComments();
        const leadingJsdoc = comments.filter(
          (comment) =>
            comment.type === "Block" &&
            comment.value.startsWith("*") &&
            comment.range !== undefined &&
            (first === undefined ||
              first.range === undefined ||
              comment.range[0] < first.range[0]),
        );
        const lastLeadingJsdoc = leadingJsdoc.at(-1);
        const lastDocumentsFirstDeclaration =
          first !== undefined &&
          declarationNeedsJsdoc(first) &&
          lastLeadingJsdoc?.loc !== undefined &&
          first.loc !== undefined &&
          lastLeadingJsdoc.loc.end.line === first.loc.start.line - 1;
        const documented = lastDocumentsFirstDeclaration
          ? leadingJsdoc.length > 1
          : leadingJsdoc.length > 0;
        if (!documented) {
          context.report({
            node,
            messageId: "missingModule",
            data: { guide: guides.module },
          });
        }
      },

      /** Require JSDoc on named function declarations. */
      FunctionDeclaration(node) {
        requireJsdoc(
          node,
          "missingCallable",
          node.id?.name ?? "default",
          guides.callable,
        );
        requireParameterTags(
          node,
          node,
          node.id?.name ?? "default",
          node.params,
        );
        requireReturnSection(
          node,
          node,
          node.id?.name ?? "default",
          node.returnType?.typeAnnotation,
        );
      },

      /** Require JSDoc on functions declared through named variables. */
      VariableDeclarator(node) {
        if (
          node.id.type !== "Identifier" ||
          (node.init?.type !== "ArrowFunctionExpression" &&
            node.init?.type !== "FunctionExpression")
        ) {
          return;
        }
        requireJsdoc(
          node.parent,
          "missingCallable",
          node.id.name,
          guides.callable,
        );
        requireParameterTags(
          node.parent,
          node.parent,
          node.id.name,
          node.init.params,
        );
        requireReturnSection(
          node.parent,
          node.parent,
          node.id.name,
          node.init.returnType?.typeAnnotation,
        );
      },

      /** Require JSDoc on module-level values that are not callables. */
      VariableDeclaration(node) {
        const parent = node.parent;
        const moduleLevel =
          parent.type === "Program" ||
          ((parent.type === "ExportNamedDeclaration" ||
            parent.type === "ExportDefaultDeclaration") &&
            parent.parent.type === "Program");
        if (!moduleLevel) {
          return;
        }
        const values = node.declarations.filter(
          (declaration) =>
            declaration.init?.type !== "ArrowFunctionExpression" &&
            declaration.init?.type !== "FunctionExpression",
        );
        if (values.length === 0) {
          return;
        }
        requireJsdoc(
          node,
          "missingGlobal",
          values
            .map((declaration) => sourceCode.getText(declaration.id))
            .join(", "),
          guides.global,
        );
      },

      /** Require JSDoc on class declarations. */
      ClassDeclaration(node) {
        requireJsdoc(
          node,
          "missingType",
          node.id?.name ?? "default",
          guides.type,
        );
        const fields = node.body.body.filter(
          (member) => member.type === "PropertyDefinition",
        );
        rejectFieldBullets(node, node.id?.name ?? "default", fields.length > 0);
        rejectFieldBlob(
          node,
          node.id?.name ?? "default",
          fields.map(propertyName),
        );
      },

      /** Require JSDoc on interface declarations. */
      TSInterfaceDeclaration(node) {
        requireJsdoc(node, "missingType", node.id.name, guides.type);
        rejectFieldBullets(node, node.id.name, node.body.body.length > 0);
        rejectFieldBlob(node, node.id.name, node.body.body.map(propertyName));
      },

      /** Require JSDoc on type aliases. */
      TSTypeAliasDeclaration(node) {
        requireJsdoc(node, "missingType", node.id.name, guides.type);
        rejectFieldBullets(
          node,
          node.id.name,
          node.typeAnnotation.type === "TSTypeLiteral" &&
            node.typeAnnotation.members.length > 0,
        );
        rejectFieldBlob(
          node,
          node.id.name,
          node.typeAnnotation.type === "TSTypeLiteral"
            ? node.typeAnnotation.members.map(propertyName)
            : [],
        );
        if (node.typeAnnotation.type === "TSFunctionType") {
          requireParameterTags(
            node,
            node,
            node.id.name,
            node.typeAnnotation.params,
          );
          requireReturnSection(
            node,
            node,
            node.id.name,
            node.typeAnnotation.returnType.typeAnnotation,
          );
        }
      },

      /** Require JSDoc on enum declarations. */
      TSEnumDeclaration(node) {
        requireJsdoc(node, "missingType", node.id.name, guides.type);
        rejectFieldBullets(node, node.id.name, node.body.members.length > 0);
        rejectFieldBlob(
          node,
          node.id.name,
          node.body.members.map(propertyName),
        );
      },

      /** Require adjacent JSDoc on interface and object-type fields. */
      TSPropertySignature(node) {
        requireJsdoc(node, "missingField", propertyName(node), guides.type);
        const annotation = node.typeAnnotation?.typeAnnotation;
        if (annotation?.type === "TSFunctionType") {
          requireParameterTags(
            node,
            node,
            propertyName(node),
            annotation.params,
          );
          requireReturnSection(
            node,
            node,
            propertyName(node),
            annotation.returnType.typeAnnotation,
          );
        }
      },

      /** Require adjacent JSDoc on declared callable fields. */
      TSMethodSignature(node) {
        requireJsdoc(node, "missingField", propertyName(node), guides.type);
        requireParameterTags(node, node, propertyName(node), node.params);
        requireReturnSection(
          node,
          node,
          propertyName(node),
          node.returnType?.typeAnnotation,
        );
      },

      /** Require adjacent JSDoc on class fields. */
      PropertyDefinition(node) {
        requireJsdoc(node, "missingField", propertyName(node), guides.type);
      },

      /** Require adjacent JSDoc on enum variants. */
      TSEnumMember(node) {
        requireJsdoc(node, "missingField", propertyName(node), guides.type);
      },

      /** Require JSDoc on concrete class methods and accessors. */
      MethodDefinition(node) {
        requireJsdoc(
          node,
          "missingCallable",
          propertyName(node),
          guides.callable,
        );
        requireParameterTags(node, node, propertyName(node), node.value.params);
        requireReturnSection(
          node,
          node,
          propertyName(node),
          node.value.returnType?.typeAnnotation,
        );
      },

      /** Require JSDoc and parameter tags on declared overload signatures. */
      TSDeclareFunction(node) {
        requireJsdoc(
          node,
          "missingCallable",
          node.id?.name ?? "default",
          guides.callable,
        );
        requireParameterTags(
          node,
          node,
          node.id?.name ?? "default",
          node.params,
        );
        requireReturnSection(
          node,
          node,
          node.id?.name ?? "default",
          node.returnType?.typeAnnotation,
        );
      },

      /** Require docs and parameter tags on anonymous call signatures. */
      TSCallSignatureDeclaration(node) {
        requireJsdoc(node, "missingField", "<call signature>", guides.type);
        requireParameterTags(node, node, "<call signature>", node.params);
        requireReturnSection(
          node,
          node,
          "<call signature>",
          node.returnType?.typeAnnotation,
        );
      },

      /** Require docs and parameter tags on anonymous construct signatures. */
      TSConstructSignatureDeclaration(node) {
        requireJsdoc(node, "missingField", "<constructor>", guides.type);
        requireParameterTags(node, node, "<constructor>", node.params);
        requireReturnSection(
          node,
          node,
          "<constructor>",
          node.returnType.typeAnnotation,
        );
      },

      /** Require adjacent docs on index signatures. */
      TSIndexSignature(node) {
        requireJsdoc(node, "missingField", "<index>", guides.type);
      },

      /** Require a local explanation before each Solid effect. */
      CallExpression(node) {
        if (
          node.callee.type !== "Identifier" ||
          node.callee.name !== "createEffect" ||
          hasEffectComment(node)
        ) {
          return;
        }
        context.report({
          node,
          messageId: "missingEffect",
          data: { guide: guides.effect },
        });
      },
    };
  },
};

// @ts-nocheck

import path from "node:path";

/**
 * Enforce the `hud/fileCard/` facade: outside that directory, the only legal
 * import from it is the facade module `fileCard/FileCard`.
 *
 * The module exports one ESLint rule. It examines every import or re-export
 * with a relative source in files outside `src/hud/fileCard/`, resolves the
 * source against the importing file, and reports any resolution landing inside
 * `src/hud/fileCard/` other than `FileCard` itself. Files inside the facade
 * directory import each other freely and are outside the rule. Bare package
 * specifiers are not facade paths and are outside the rule; the project uses
 * no path aliases, so every project-internal import is relative.
 *
 * The rule owns only per-file analysis state created by ESLint. It does not
 * resolve packages, follow re-export chains, check what the facade itself
 * imports from the rest of the application, or alter source.
 */
export const fileCardFacadeRule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Outside hud/fileCard/, import only the fileCard/FileCard facade.",
    },
    schema: [],
    messages: {
      deepImport:
        "'{{source}}' reaches inside hud/fileCard/. Code outside the facade imports only fileCard/FileCard.",
    },
  },

  /**
   * Analyze one parsed module against the facade boundary.
   *
   * @param {import("eslint").Rule.RuleContext} context ESLint's per-file rule
   * context.
   * @returns {import("eslint").Rule.RuleListener} Visitors that report every
   * relative import or re-export resolving past the facade.
   */
  create(context) {
    const facadeDir = path.join("src", "hud", "fileCard");
    const facadeModule = path.join(facadeDir, "FileCard");
    const importerDir = path.dirname(
      path.relative(context.cwd, context.filename),
    );

    // Facade-internal files import each other freely.
    if (
      importerDir === facadeDir ||
      importerDir.startsWith(facadeDir + path.sep)
    ) {
      return {};
    }

    /**
     * Report one import or re-export whose relative source resolves inside the
     * facade directory to anything but the facade module.
     *
     * @param {import("estree").Node} node The declaration carrying `source`.
     * @param {string} source The literal module specifier.
     */
    function checkSource(node, source) {
      if (!source.startsWith("./") && !source.startsWith("../")) {
        return;
      }
      const resolved = path.normalize(path.join(importerDir, source));
      if (
        resolved !== facadeModule &&
        (resolved === facadeDir || resolved.startsWith(facadeDir + path.sep))
      ) {
        context.report({ node, messageId: "deepImport", data: { source } });
      }
    }

    return {
      ImportDeclaration(node) {
        checkSource(node, node.source.value);
      },
      ExportNamedDeclaration(node) {
        if (node.source !== null) {
          checkSource(node, node.source.value);
        }
      },
      ExportAllDeclaration(node) {
        checkSource(node, node.source.value);
      },
    };
  },
};

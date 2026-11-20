// @ts-nocheck

import path from "node:path";

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

  create(context) {
    const facadeDir = path.join("src", "hud", "fileCard");
    const facadeModule = path.join(facadeDir, "FileCard");
    const importerDir = path.dirname(
      path.relative(context.cwd, context.filename),
    );

    if (
      importerDir === facadeDir ||
      importerDir.startsWith(facadeDir + path.sep)
    ) {
      return {};
    }

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

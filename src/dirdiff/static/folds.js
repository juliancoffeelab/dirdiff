(function (globalScope) {
    function normalizeFoldHints(foldHints, rowCount) {
        if (!Array.isArray(foldHints) || !foldHints.length) {
            return [];
        }

        return foldHints
            .map((hint) => ({
                startRow: Number(hint.start_row),
                endRow: Number(hint.end_row),
                label: String(hint.label || "").trim(),
            }))
            .filter((hint) =>
                Number.isInteger(hint.startRow)
                && Number.isInteger(hint.endRow)
                && hint.startRow >= 0
                && hint.endRow <= rowCount
                && hint.endRow > hint.startRow,
            )
            .sort((left, right) => (
                left.startRow - right.startRow || left.endRow - right.endRow
            ));
    }

    function addFoldRows(rows, foldHints) {
        const normalized = normalizeFoldHints(foldHints, rows.length);
        if (!normalized.length) {
            return rows;
        }

        const result = [];
        let cursor = 0;

        for (const hint of normalized) {
            if (hint.startRow < cursor) {
                continue;
            }

            result.push(...rows.slice(cursor, hint.startRow));
            const foldedRows = rows.slice(hint.startRow, hint.endRow);
            if (foldedRows.length) {
                result.push({
                    status: "fold",
                    count: foldedRows.length,
                    foldedRows,
                    label: hint.label,
                });
            }
            cursor = hint.endRow;
        }

        result.push(...rows.slice(cursor));
        return result;
    }

    const api = {
        addFoldRows,
        normalizeFoldHints,
    };

    globalScope.fileDiffFolds = api;

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== "undefined" ? globalThis : window);

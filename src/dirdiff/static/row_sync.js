(function (globalScope) {
    function resetRowMinHeight(row) {
        if (row?.style) {
            row.style.minHeight = "";
        }
    }

    function syncRowHeights(leftRows, rightRows) {
        const totalRows = Math.max(leftRows.length, rightRows.length);

        for (let index = 0; index < totalRows; index += 1) {
            const leftRow = leftRows[index];
            const rightRow = rightRows[index];

            resetRowMinHeight(leftRow);
            resetRowMinHeight(rightRow);

            if (!leftRow || !rightRow) {
                continue;
            }

            const targetHeight = Math.ceil(
                Math.max(leftRow.offsetHeight, rightRow.offsetHeight),
            );
            const minHeight = `${targetHeight}px`;
            leftRow.style.minHeight = minHeight;
            rightRow.style.minHeight = minHeight;
        }
    }

    function collectDirectDiffRows(container) {
        if (!container) {
            return [];
        }

        return Array.from(container.children).filter((child) =>
            child.classList?.contains("diff-row"),
        );
    }

    function syncDiffRowHeights(leftContainer, rightContainer) {
        const leftRows = collectDirectDiffRows(leftContainer);
        const rightRows = collectDirectDiffRows(rightContainer);
        syncRowHeights(leftRows, rightRows);
    }

    const api = {
        collectDirectDiffRows,
        syncDiffRowHeights,
        syncRowHeights,
    };

    globalScope.fileDiffRowSync = api;

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== "undefined" ? globalThis : window);

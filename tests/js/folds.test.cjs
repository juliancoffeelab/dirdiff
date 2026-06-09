const test = require("node:test");
const assert = require("node:assert/strict");

const folds = require("../../src/dirdiff/static/folds.js");

test("normalizeFoldHints drops invalid ranges and sorts by row order", () => {
    const normalized = folds.normalizeFoldHints([
        { start_row: 8, end_row: 10, label: "later" },
        { start_row: 3, end_row: 3, label: "empty" },
        { start_row: -1, end_row: 2, label: "negative" },
        { start_row: 2, end_row: 5, label: "early" },
    ], 12);

    assert.deepEqual(normalized, [
        { startRow: 2, endRow: 5, label: "early" },
        { startRow: 8, endRow: 10, label: "later" },
    ]);
});

test("addFoldRows inserts synthetic fold rows with folded payload slices", () => {
    const rows = [
        { status: "equal", right_text: "def helper():" },
        { status: "equal", right_text: "    value = 1" },
        { status: "equal", right_text: "    return value" },
        { status: "replace", right_text: "x = 2" },
    ];

    const processed = folds.addFoldRows(rows, [
        { start_row: 1, end_row: 3, label: "def helper():" },
    ]);

    assert.equal(processed.length, 3);
    assert.equal(processed[0], rows[0]);
    assert.deepEqual(processed[1], {
        status: "fold",
        count: 2,
        foldedRows: rows.slice(1, 3),
        label: "def helper():",
    });
    assert.equal(processed[2], rows[3]);
});

test("addFoldRows suppresses nested hints when an outer fold already consumed the rows", () => {
    const rows = Array.from({ length: 8 }, (_, index) => ({
        status: "equal",
        right_text: `row ${index}`,
    }));

    const processed = folds.addFoldRows(rows, [
        { start_row: 1, end_row: 6, label: "outer" },
        { start_row: 2, end_row: 4, label: "inner" },
    ]);

    assert.equal(processed.length, 4);
    assert.deepEqual(processed[1], {
        status: "fold",
        count: 5,
        foldedRows: rows.slice(1, 6),
        label: "outer",
    });
});

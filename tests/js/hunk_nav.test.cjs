const test = require("node:test");
const assert = require("node:assert/strict");

const nav = require("../../src/dirdiff/static/hunk_nav.js");

test("uniqueSortedPositions sorts and dedupes near-identical positions", () => {
    const positions = nav.uniqueSortedPositions([320, 120, 124, 540, NaN, 541], 6);

    assert.deepEqual(positions, [120, 320, 540]);
});

test("findNearestIndex picks the closest position to viewport center", () => {
    const positions = [120, 320, 540];

    assert.equal(nav.findNearestIndex(positions, 100), 0);
    assert.equal(nav.findNearestIndex(positions, 430), 1);
});

test("pickTargetIndex steps relative to the nearest visible hunk", () => {
    const positions = [120, 320, 540];

    assert.equal(nav.pickTargetIndex(positions, 0, "next"), 0);
    assert.equal(nav.pickTargetIndex(positions, 260, "next"), 2);
    assert.equal(nav.pickTargetIndex(positions, 260, "prev"), 0);
});

test("pickTargetIndex wraps when viewport center is beyond the last hunk", () => {
    const positions = [120, 320, 540];

    assert.equal(nav.pickTargetIndex(positions, 800, "next"), 0);
    assert.equal(nav.pickTargetIndex(positions, 800, "prev"), 2);
});

test("stepHunkIndex wraps in both directions", () => {
    assert.equal(nav.stepHunkIndex(0, "prev", 3), 2);
    assert.equal(nav.stepHunkIndex(2, "next", 3), 0);
});

test("pickTargetPosition returns the selected hunk position", () => {
    const positions = [120, 320, 540];

    assert.equal(nav.pickTargetPosition(positions, 260, "next"), 540);
    assert.equal(nav.pickTargetPosition(positions, 260, "prev"), 120);
});

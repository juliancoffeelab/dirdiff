const test = require("node:test");
const assert = require("node:assert/strict");

const nav = require("../../src/dirdiff/static/hunk_nav.js");

test("normalizeSnapshot sorts, dedupes, and computes currentPosition", () => {
    const snapshot = nav.normalizeSnapshot({
        positions: [320, 120, 124, 540],
        scrollY: 80,
        maxScrollTop: 900,
    });

    assert.deepEqual(snapshot.positions, [120, 320, 540]);
    assert.equal(snapshot.signature, "120|320|540");
    assert.equal(snapshot.currentPosition, 200);
    assert.equal(snapshot.maxScrollTop, 900);
});

test("targetScrollTopForPosition clamps to the top and bottom of the document", () => {
    assert.equal(nav.targetScrollTopForPosition(90, 800), 0);
    assert.equal(nav.targetScrollTopForPosition(640, 800), 520);
    assert.equal(nav.targetScrollTopForPosition(1200, 800), 800);
});

test("next navigation targets the first hunk below the current scroll anchor", () => {
    const positions = [120, 320, 540];

    assert.equal(nav.pickRelativeIndex(positions, 0, "next"), 0);
    assert.equal(nav.pickRelativeIndex(positions, 260, "next"), 1);
});

test("previous navigation targets the closest earlier hunk and wraps", () => {
    const positions = [120, 320, 540];

    assert.equal(nav.pickRelativeIndex(positions, 320, "prev"), 0);
    assert.equal(nav.pickRelativeIndex(positions, 0, "prev"), 2);
});

test("stepHunkIndex wraps in both directions", () => {
    assert.equal(nav.stepHunkIndex(0, "prev", 3), 2);
    assert.equal(nav.stepHunkIndex(2, "next", 3), 0);
});

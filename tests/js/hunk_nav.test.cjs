const test = require("node:test");
const assert = require("node:assert/strict");

const nav = require("../../src/dirdiff/static/hunk_nav.js");

test("next navigation targets the first hunk below the current scroll anchor", () => {
    const positions = [120, 320, 540];

    assert.equal(nav.pickRelativeIndex(positions, 0, "next"), 0);
});

test("next navigation advances to the immediate next hunk after landing on one", () => {
    const positions = [200, 450, 700];

    assert.equal(nav.pickRelativeIndex(positions, 200, "next"), 1);
});

test("previous navigation targets the closest earlier hunk and wraps", () => {
    const positions = [120, 320, 540];

    assert.equal(nav.pickRelativeIndex(positions, 320, "prev"), 0);
    assert.equal(nav.pickRelativeIndex(positions, 0, "prev"), 2);
});

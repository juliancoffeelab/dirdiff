const test = require("node:test");
const assert = require("node:assert/strict");

const nav = require("../../src/dirdiff/static/hunk_nav.js");

function snapshot({
    positions,
    scrollY = 0,
    maxScrollTop = 1200,
} = {}) {
    return {
        positions,
        scrollY,
        maxScrollTop,
    };
}

function request(state, direction, rawSnapshot) {
    return nav.reduceState(state, {
        type: "REQUEST_NAVIGATION",
        direction,
        snapshot: rawSnapshot,
    });
}

test("active index is usable during auto-scroll even before scroll settles", () => {
    const rawSnapshot = snapshot({
        positions: [200, 440, 700],
        scrollY: 80,
    });
    const normalized = nav.normalizeSnapshot(rawSnapshot);
    const state = {
        ...nav.createInitialState(),
        activeIndex: 1,
        signature: normalized.signature,
        autoScrollInProgress: true,
    };

    assert.equal(nav.isActiveIndexUsable(state, normalized), true);
});

test("active index remains usable when the viewport is bottom-clamped at the target", () => {
    const rawSnapshot = snapshot({
        positions: [240, 600, 1120],
        scrollY: 1000,
        maxScrollTop: 1000,
    });
    const normalized = nav.normalizeSnapshot(rawSnapshot);
    const state = {
        ...nav.createInitialState(),
        activeIndex: 2,
        signature: normalized.signature,
    };

    assert.equal(nav.isActiveIndexUsable(state, normalized), true);
});

test("request navigation is a no-op when no hunk anchors exist", () => {
    const { state, effect } = request(
        nav.createInitialState(),
        "next",
        snapshot({ positions: [] }),
    );

    assert.equal(effect, null);
    assert.equal(state.activeIndex, null);
    assert.equal(state.signature, "");
});

test("first next request chooses the first hunk below the current scroll anchor", () => {
    const { state, effect } = request(
        nav.createInitialState(),
        "next",
        snapshot({
            positions: [220, 500, 760],
            scrollY: 0,
            maxScrollTop: 900,
        }),
    );

    assert.equal(effect.targetIndex, 0);
    assert.equal(effect.top, 100);
    assert.equal(state.activeIndex, 0);
    assert.equal(state.autoScrollInProgress, true);
});

test("first previous request wraps to the last hunk when starting above the first anchor", () => {
    const { state, effect } = request(
        nav.createInitialState(),
        "prev",
        snapshot({
            positions: [220, 500, 760],
            scrollY: 0,
            maxScrollTop: 900,
        }),
    );

    assert.equal(effect.targetIndex, 2);
    assert.equal(state.activeIndex, 2);
});

test("repeated next requests during auto-scroll advance deterministically", () => {
    const first = request(
        nav.createInitialState(),
        "next",
        snapshot({
            positions: [220, 500, 760],
            scrollY: 0,
            maxScrollTop: 900,
        }),
    );
    const second = request(
        first.state,
        "next",
        snapshot({
            positions: [220, 500, 760],
            scrollY: 40,
            maxScrollTop: 900,
        }),
    );
    const third = request(
        second.state,
        "next",
        snapshot({
            positions: [220, 500, 760],
            scrollY: 90,
            maxScrollTop: 900,
        }),
    );

    assert.equal(second.effect.targetIndex, 1);
    assert.equal(third.effect.targetIndex, 2);
    assert.equal(third.state.pendingDirections.length, 0);
});

test("rapid alternating requests preserve sequential move semantics", () => {
    let state = nav.createInitialState();
    const rawSnapshot = snapshot({
        positions: [220, 500, 760],
        scrollY: 0,
        maxScrollTop: 900,
    });

    state = request(state, "next", rawSnapshot).state;
    state = request(state, "next", rawSnapshot).state;
    state = request(state, "prev", rawSnapshot).state;
    const final = request(state, "next", rawSnapshot);

    assert.equal(final.effect.targetIndex, 1);
    assert.equal(final.state.activeIndex, 1);
});

test("signature changes invalidate the active index and recompute from scroll position", () => {
    const original = nav.normalizeSnapshot(
        snapshot({
            positions: [220, 500, 760],
            scrollY: 380,
            maxScrollTop: 900,
        }),
    );
    const state = {
        ...nav.createInitialState(),
        activeIndex: 1,
        signature: original.signature,
    };

    const next = request(
        state,
        "next",
        snapshot({
            positions: [220, 760],
            scrollY: 380,
            maxScrollTop: 900,
        }),
    );

    assert.equal(next.effect.targetIndex, 1);
    assert.equal(next.state.activeIndex, 1);
});

test("settled event clears the auto-scroll flag without dropping the active target", () => {
    const first = request(
        nav.createInitialState(),
        "next",
        snapshot({
            positions: [220, 500, 760],
            scrollY: 0,
            maxScrollTop: 900,
        }),
    );

    const settled = nav.reduceState(first.state, {
        type: "SETTLED",
        snapshot: snapshot({
            positions: [220, 500, 760],
            scrollY: 100,
            maxScrollTop: 900,
        }),
    });

    assert.equal(settled.state.autoScrollInProgress, false);
    assert.equal(settled.state.activeIndex, 0);
});

test("reset clears all navigation state", () => {
    const dirtyState = {
        activeIndex: 2,
        signature: "120|360|720",
        autoScrollInProgress: true,
        currentTargetIndex: 2,
        currentTargetTop: 600,
        pendingDirections: ["next"],
    };

    const reset = nav.reduceState(dirtyState, { type: "RESET" });

    assert.deepEqual(reset.state, nav.createInitialState());
});

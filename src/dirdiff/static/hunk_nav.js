(function (globalScope) {
    const DEFAULT_SCROLL_MARGIN = 120;
    const DEFAULT_ACTIVE_TOLERANCE = 24;
    const DEFAULT_SETTLE_DELAY_MS = 260;

    function uniqueSortedPositions(positions, tolerance = 6) {
        const sorted = positions
            .filter((value) => Number.isFinite(value))
            .sort((a, b) => a - b);
        const unique = [];

        for (const position of sorted) {
            const last = unique[unique.length - 1];
            if (last === undefined || Math.abs(position - last) > tolerance) {
                unique.push(position);
            }
        }

        return unique;
    }

    function positionsSignature(positions) {
        return positions.join("|");
    }

    function stepHunkIndex(currentIndex, direction, length) {
        if (!Number.isInteger(currentIndex) || length <= 0) {
            return null;
        }

        return direction === "next"
            ? (currentIndex + 1) % length
            : (currentIndex - 1 + length) % length;
    }

    function pickRelativeIndex(positions, currentPosition, direction, tolerance = 24) {
        if (!positions.length) {
            return null;
        }

        if (direction === "next") {
            for (let index = 0; index < positions.length; index += 1) {
                if (positions[index] > currentPosition + tolerance) {
                    return index;
                }
            }
            return 0;
        }

        for (let index = positions.length - 1; index >= 0; index -= 1) {
            if (positions[index] < currentPosition - tolerance) {
                return index;
            }
        }
        return positions.length - 1;
    }

    function targetScrollTopForPosition(
        position,
        maxScrollTop,
        scrollMargin = DEFAULT_SCROLL_MARGIN,
    ) {
        return Math.min(Math.max(position - scrollMargin, 0), Math.max(maxScrollTop, 0));
    }

    function normalizeSnapshot(
        snapshot,
        {
            scrollMargin = DEFAULT_SCROLL_MARGIN,
            dedupeTolerance = 6,
        } = {},
    ) {
        const positions = uniqueSortedPositions(snapshot.positions || [], dedupeTolerance);
        const scrollY = Number(snapshot.scrollY || 0);
        const maxScrollTop = Math.max(Number(snapshot.maxScrollTop || 0), 0);

        return {
            positions,
            signature: positionsSignature(positions),
            scrollY,
            currentPosition: scrollY + scrollMargin,
            maxScrollTop,
        };
    }

    function createInitialState() {
        return {
            activeIndex: null,
            signature: "",
            autoScrollInProgress: false,
            currentTargetIndex: null,
            currentTargetTop: 0,
            pendingDirections: [],
        };
    }

    function cloneState(state) {
        return {
            activeIndex: state.activeIndex,
            signature: state.signature,
            autoScrollInProgress: !!state.autoScrollInProgress,
            currentTargetIndex: state.currentTargetIndex,
            currentTargetTop: state.currentTargetTop || 0,
            pendingDirections: [...(state.pendingDirections || [])],
        };
    }

    function isActiveIndexUsable(
        state,
        snapshot,
        {
            scrollMargin = DEFAULT_SCROLL_MARGIN,
            activeTolerance = DEFAULT_ACTIVE_TOLERANCE,
        } = {},
    ) {
        if (!Number.isInteger(state.activeIndex)) {
            return false;
        }
        if (state.activeIndex < 0 || state.activeIndex >= snapshot.positions.length) {
            return false;
        }
        if (state.signature !== snapshot.signature) {
            return false;
        }
        if (state.autoScrollInProgress) {
            return true;
        }

        const activePosition = snapshot.positions[state.activeIndex];
        const activeScrollTop = targetScrollTopForPosition(
            activePosition,
            snapshot.maxScrollTop,
            scrollMargin,
        );

        return (
            Math.abs(activePosition - snapshot.currentPosition) <= activeTolerance
            || Math.abs(activeScrollTop - snapshot.scrollY) <= activeTolerance
        );
    }

    function resolveTargetIndex(
        state,
        snapshot,
        {
            scrollMargin = DEFAULT_SCROLL_MARGIN,
            activeTolerance = DEFAULT_ACTIVE_TOLERANCE,
        } = {},
    ) {
        if (!snapshot.positions.length || !state.pendingDirections.length) {
            return null;
        }

        let targetIndex = isActiveIndexUsable(
            state,
            snapshot,
            { scrollMargin, activeTolerance },
        )
            ? state.activeIndex
            : null;

        for (const direction of state.pendingDirections) {
            targetIndex = targetIndex === null
                ? pickRelativeIndex(
                    snapshot.positions,
                    snapshot.currentPosition,
                    direction,
                    activeTolerance,
                )
                : stepHunkIndex(targetIndex, direction, snapshot.positions.length);
        }

        return targetIndex;
    }

    function sameActiveIndex(left, right) {
        const leftIndex = Number.isInteger(left?.activeIndex) ? left.activeIndex : null;
        const rightIndex = Number.isInteger(right?.activeIndex) ? right.activeIndex : null;
        return leftIndex === rightIndex;
    }

    function reduceState(
        state,
        event,
        {
            scrollMargin = DEFAULT_SCROLL_MARGIN,
            activeTolerance = DEFAULT_ACTIVE_TOLERANCE,
        } = {},
    ) {
        const current = cloneState(state);

        if (event.type === "RESET") {
            return {
                state: createInitialState(),
                effect: null,
            };
        }

        if (event.type === "SCROLL") {
            if (!current.autoScrollInProgress) {
                return { state: current, effect: null };
            }

            const snapshot = normalizeSnapshot(
                event.snapshot,
                { scrollMargin },
            );
            current.signature = snapshot.signature;
            return { state: current, effect: null };
        }

        if (event.type === "SETTLED") {
            const snapshot = normalizeSnapshot(
                event.snapshot,
                { scrollMargin },
            );

            current.signature = snapshot.signature;
            current.autoScrollInProgress = false;

            if (
                !Number.isInteger(current.activeIndex)
                || current.activeIndex < 0
                || current.activeIndex >= snapshot.positions.length
            ) {
                current.activeIndex = null;
                current.currentTargetIndex = null;
                current.currentTargetTop = 0;
            }

            return { state: current, effect: null };
        }

        if (event.type !== "REQUEST_NAVIGATION") {
            throw new Error(`Unsupported hunk navigation event: ${event.type}`);
        }

        const snapshot = normalizeSnapshot(
            event.snapshot,
            { scrollMargin },
        );
        current.signature = snapshot.signature;
        current.pendingDirections.push(event.direction);

        const targetIndex = resolveTargetIndex(
            current,
            snapshot,
            { scrollMargin, activeTolerance },
        );

        if (targetIndex === null) {
            current.pendingDirections = [];
            return { state: current, effect: null };
        }

        current.pendingDirections = [];
        current.activeIndex = targetIndex;
        current.currentTargetIndex = targetIndex;
        current.currentTargetTop = targetScrollTopForPosition(
            snapshot.positions[targetIndex],
            snapshot.maxScrollTop,
            scrollMargin,
        );
        current.autoScrollInProgress = true;

        return {
            state: current,
            effect: {
                type: "scrollTo",
                top: current.currentTargetTop,
                behavior: event.behavior || "smooth",
                targetIndex,
            },
        };
    }

    function createHunkNavigationController(
        adapter,
        {
            scrollMargin = DEFAULT_SCROLL_MARGIN,
            activeTolerance = DEFAULT_ACTIVE_TOLERANCE,
            settleDelayMs = DEFAULT_SETTLE_DELAY_MS,
            scrollBehavior = "smooth",
            onStateChange = () => {},
            onActiveIndexChange = () => {},
        } = {},
    ) {
        let state = createInitialState();
        let settleTimerId = 0;
        let lastSnapshot = null;

        function normalizeAndRemember(snapshot) {
            lastSnapshot = normalizeSnapshot(
                snapshot,
                { scrollMargin },
            );
            return lastSnapshot;
        }

        function readSnapshot() {
            return normalizeAndRemember(adapter.readSnapshot());
        }

        function clearSettleTimer() {
            if (!settleTimerId) {
                return;
            }
            adapter.clearTimeout(settleTimerId);
            settleTimerId = 0;
        }

        function scheduleSettle() {
            clearSettleTimer();
            settleTimerId = adapter.setTimeout(() => {
                settleTimerId = 0;
                const previousState = cloneState(state);
                const result = reduceState(
                    state,
                    { type: "SETTLED", snapshot: readSnapshot() },
                    { scrollMargin, activeTolerance },
                );
                state = result.state;
                if (!sameActiveIndex(previousState, state)) {
                    onActiveIndexChange(
                        Number.isInteger(state.activeIndex) ? state.activeIndex : null,
                        previousState,
                        cloneState(state),
                    );
                }
                onStateChange(cloneState(state));
            }, settleDelayMs);
        }

        function applyResult(result) {
            const previousState = cloneState(state);
            state = result.state;
            if (!sameActiveIndex(previousState, state)) {
                onActiveIndexChange(
                    Number.isInteger(state.activeIndex) ? state.activeIndex : null,
                    previousState,
                    cloneState(state),
                );
            }
            onStateChange(cloneState(state));
            if (!result.effect) {
                return false;
            }

            adapter.scrollTo(result.effect.top, result.effect.behavior || scrollBehavior);
            scheduleSettle();
            return true;
        }

        return {
            reset() {
                clearSettleTimer();
                const previousState = cloneState(state);
                state = reduceState(
                    state,
                    { type: "RESET" },
                    { scrollMargin, activeTolerance },
                ).state;
                if (!sameActiveIndex(previousState, state)) {
                    onActiveIndexChange(
                        Number.isInteger(state.activeIndex) ? state.activeIndex : null,
                        previousState,
                        cloneState(state),
                    );
                }
                onStateChange(cloneState(state));
            },
            request(direction) {
                const result = reduceState(
                    state,
                    {
                        type: "REQUEST_NAVIGATION",
                        direction,
                        snapshot: readSnapshot(),
                        behavior: scrollBehavior,
                    },
                    { scrollMargin, activeTolerance },
                );
                return applyResult(result);
            },
            handleScroll() {
                if (!state.autoScrollInProgress) {
                    return;
                }
                scheduleSettle();
            },
            getState() {
                return cloneState(state);
            },
        };
    }

    const api = {
        DEFAULT_ACTIVE_TOLERANCE,
        DEFAULT_SCROLL_MARGIN,
        DEFAULT_SETTLE_DELAY_MS,
        createHunkNavigationController,
        createInitialState,
        isActiveIndexUsable,
        normalizeSnapshot,
        pickRelativeIndex,
        positionsSignature,
        reduceState,
        resolveTargetIndex,
        stepHunkIndex,
        targetScrollTopForPosition,
        uniqueSortedPositions,
    };

    globalScope.fileDiffNav = api;

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== "undefined" ? globalThis : window);

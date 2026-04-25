(function (globalScope) {
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

    function findNearestIndex(positions, viewportCenter) {
        if (!positions.length) {
            return null;
        }

        let nearestIndex = 0;
        let nearestDistance = Math.abs(positions[0] - viewportCenter);
        for (let i = 1; i < positions.length; i += 1) {
            const distance = Math.abs(positions[i] - viewportCenter);
            if (distance < nearestDistance) {
                nearestDistance = distance;
                nearestIndex = i;
            }
        }

        return nearestIndex;
    }

    function stepHunkIndex(currentIndex, direction, length) {
        if (!Number.isInteger(currentIndex) || length <= 0) {
            return null;
        }

        return direction === "next"
            ? (currentIndex + 1) % length
            : (currentIndex - 1 + length) % length;
    }

    function pickTargetIndex(positions, viewportCenter, direction) {
        if (!positions.length) {
            return null;
        }

        const firstPosition = positions[0];
        const lastPosition = positions[positions.length - 1];
        if (viewportCenter < firstPosition) {
            return direction === "next" ? 0 : positions.length - 1;
        }
        if (viewportCenter > lastPosition) {
            return direction === "next" ? 0 : positions.length - 1;
        }

        const nearestIndex = findNearestIndex(positions, viewportCenter);
        return stepHunkIndex(nearestIndex, direction, positions.length);
    }

    const api = {
        findNearestIndex,
        uniqueSortedPositions,
        stepHunkIndex,
        pickTargetIndex,
    };

    globalScope.fileDiffNav = api;

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== "undefined" ? globalThis : window);

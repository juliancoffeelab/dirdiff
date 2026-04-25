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

    function pickRelativeIndex(positions, currentPosition, direction, tolerance = 24) {
        if (!positions.length) {
            return null;
        }

        // Navigate from the current scroll anchor so tall viewports do not skip
        // nearby hunks that happen to sit above the viewport center.
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

    const api = {
        findNearestIndex,
        uniqueSortedPositions,
        stepHunkIndex,
        pickRelativeIndex,
    };

    globalScope.fileDiffNav = api;

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== "undefined" ? globalThis : window);

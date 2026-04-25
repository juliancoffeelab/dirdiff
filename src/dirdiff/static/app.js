const form = document.getElementById("controlsForm");
const directFilesForm = document.getElementById("directFilesForm");
const modeSelect = document.getElementById("modeSelect");
const pathInput = document.getElementById("pathInput");
const customRefsGroup = document.getElementById("customRefsGroup");
const leftRefInput = document.getElementById("leftRefInput");
const rightRefInput = document.getElementById("rightRefInput");
const leftFileInput = document.getElementById("leftFileInput");
const rightFileInput = document.getElementById("rightFileInput");
const directFilesPanel = document.getElementById("directFilesPanel");
const statusText = document.getElementById("statusText");
const summaryGrid = document.getElementById("summaryGrid");
const resultPanel = document.getElementById("resultPanel");
const prevHunkBtn = document.getElementById("prevHunkBtn");
const nextHunkBtn = document.getElementById("nextHunkBtn");

const rowSyncApi = window.fileDiffRowSync || {};
const registeredRowSyncs = window.__fileDiffRowSyncHandlers
    || (window.__fileDiffRowSyncHandlers = new Set());
const BUILTIN_SIDES = new Set(["head", "index", "worktree"]);
const MODE_TO_SIDES = {
    files: ["index", "worktree"],
    staged: ["head", "index"],
    "against-head": ["head", "worktree"],
};
const hunkNavState = {
    activeIndex: null,
    signature: "",
    lastNavAt: 0,
};
let pendingLoadTimer = 0;
let activeLoadToken = 0;

if (!window.__fileDiffRowSyncListenerBound) {
    window.addEventListener("resize", () => {
        for (const syncRows of registeredRowSyncs) {
            syncRows();
        }
    });
    window.__fileDiffRowSyncListenerBound = true;
}

function registerRowSync(syncRows) {
    registeredRowSyncs.add(syncRows);
    return () => {
        registeredRowSyncs.delete(syncRows);
    };
}

function makeScheduledRowSync(leftLines, rightLines) {
    let frameId = 0;
    let unregister = () => {};

    const runSync = () => {
        frameId = 0;
        if (!document.body.contains(leftLines) || !document.body.contains(rightLines)) {
            unregister();
            return;
        }
        rowSyncApi.syncDiffRowHeights?.(leftLines, rightLines);
    };

    unregister = registerRowSync(runSync);

    return () => {
        if (frameId) {
            cancelAnimationFrame(frameId);
        }
        frameId = requestAnimationFrame(runSync);
    };
}

function escapeHtml(value) {
    const node = document.createElement("div");
    node.textContent = value;
    return node.innerHTML;
}

function summaryItem(label, value) {
    const wrapper = document.createElement("div");
    wrapper.className = "summary-item";
    wrapper.innerHTML = `
        <span class="summary-label">${escapeHtml(label)}</span>
        <span class="summary-value">${escapeHtml(String(value))}</span>
    `;
    return wrapper;
}

function summaryDelta(symbol, value, kind, tooltip) {
    const node = document.createElement("span");
    node.className = `summary-delta summary-delta-${kind}`;
    node.title = tooltip;
    node.setAttribute("aria-label", tooltip);
    node.innerHTML = `<span class="summary-delta-symbol">${escapeHtml(symbol)}</span>${escapeHtml(String(value))}`;
    return node;
}

function summaryCluster(label, items, tooltip) {
    const wrapper = document.createElement("div");
    wrapper.className = "summary-cluster";
    wrapper.title = tooltip;

    const heading = document.createElement("span");
    heading.className = "summary-cluster-label";
    heading.textContent = label;

    const values = document.createElement("div");
    values.className = "summary-cluster-values";
    values.append(...items);

    wrapper.append(heading, values);
    return wrapper;
}

function renderSummary(summary, mode) {
    if (mode === "repo") {
        summaryGrid.replaceChildren(
            summaryCluster(
                "Files",
                [
                    summaryDelta("+", summary.added_files || 0, "added", "Files added"),
                    summaryDelta("~", summary.updated_files || 0, "updated", "Files updated"),
                    summaryDelta("-", summary.removed_files || 0, "removed", "Files removed"),
                ],
                "Repository file totals",
            ),
            summaryCluster(
                "Lines",
                [
                    summaryDelta("+", summary.added_lines, "added", "Lines added"),
                    summaryDelta("~", summary.modified_lines, "updated", "Lines updated"),
                    summaryDelta("-", summary.removed_lines, "removed", "Lines removed"),
                ],
                "Repository line totals",
            ),
        );
        return;
    }

    summaryGrid.replaceChildren(
        summaryCluster(
            "Lines",
            [
                summaryDelta("+", summary.added_lines, "added", "Lines added"),
                summaryDelta("~", summary.modified_lines, "updated", "Lines updated"),
                summaryDelta("-", summary.removed_lines, "removed", "Lines removed"),
            ],
            "Line totals for this diff",
        ),
    );
}

function setStatus(message, isError = false) {
    statusText.textContent = message;
    statusText.className = isError ? "status error-text" : "status";
}

function wrapChangedRange(root, start, end, className, title = "") {
    if (start >= end) return;

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const targets = [];
    let position = 0;
    let node = walker.nextNode();

    while (node) {
        const length = node.textContent.length;
        const nodeStart = position;
        const nodeEnd = position + length;

        if (end <= nodeStart) break;
        if (start < nodeEnd && end > nodeStart && length > 0) {
            targets.push({
                node,
                localStart: Math.max(0, start - nodeStart),
                localEnd: Math.min(length, end - nodeStart),
            });
        }

        position = nodeEnd;
        node = walker.nextNode();
    }

    for (let index = targets.length - 1; index >= 0; index -= 1) {
        const target = targets[index];
        let currentNode = target.node;

        if (target.localEnd < currentNode.textContent.length) {
            currentNode.splitText(target.localEnd);
        }
        if (target.localStart > 0) {
            currentNode = currentNode.splitText(target.localStart);
        }

        const wrapper = document.createElement("span");
        wrapper.className = className;
        if (title) wrapper.title = title;
        currentNode.parentNode.replaceChild(wrapper, currentNode);
        wrapper.append(currentNode);
    }
}

function decorateTokenDiff(contentEl, tokens) {
    let offset = 0;
    let sawContent = false;

    for (const tok of tokens) {
        const text = String(tok.text || "");
        const isWs = !!tok.is_ws;
        const start = offset;
        const end = start + text.length;

        if (tok.changed && text.length > 0) {
            const className =
                isWs && !sawContent
                    ? "token-changed whitespace-leading"
                    : isWs
                    ? "token-changed whitespace"
                    : "token-changed";
            const title = isWs && !sawContent ? "indentation changed" : "";
            wrapChangedRange(contentEl, start, end, className, title);
        }

        if (!isWs && text.length > 0) {
            sawContent = true;
        }
        offset = end;
    }
}

function renderSyntaxText(contentEl, text, syntaxSpans) {
    if (!syntaxSpans || !syntaxSpans.length) {
        contentEl.textContent = text || " ";
        return;
    }

    let cursor = 0;
    for (const span of syntaxSpans) {
        const start = Math.max(0, Number(span.start || 0));
        const end = Math.min(text.length, Number(span.end || 0));
        if (start > cursor) {
            contentEl.append(document.createTextNode(text.slice(cursor, start)));
        }
        if (end > start) {
            const node = document.createElement("span");
            node.className = ["ts-token", ...(span.classes || [])].join(" ");
            node.textContent = text.slice(start, end);
            contentEl.append(node);
        }
        cursor = Math.max(cursor, end);
    }

    if (cursor < text.length) {
        contentEl.append(document.createTextNode(text.slice(cursor)));
    }
    if (!contentEl.childNodes.length) {
        contentEl.textContent = text || " ";
    }
}

function makeDiffRow(row, side, markHunkAnchor = false) {
    const rowEl = document.createElement("div");
    rowEl.className = `diff-row ${row.status}`;
    rowEl.classList.add(`side-${side}`);

    if (
        (row.status === "insert" && side === "left")
        || (row.status === "delete" && side === "right")
    ) {
        rowEl.classList.add("empty-side");
    }
    if (
        markHunkAnchor
        && (row.status === "insert" || row.status === "delete" || row.status === "replace")
    ) {
        rowEl.classList.add("hunk-anchor");
    }

    const noEl = document.createElement("div");
    noEl.className = "line-no";
    noEl.textContent = (side === "left" ? row.left_no : row.right_no) ?? "";

    const codeEl = document.createElement("code");
    codeEl.className = "line-code";
    const contentEl = document.createElement("span");
    contentEl.className = "line-code-content";

    const tokens = side === "left" ? row.left_tokens : row.right_tokens;
    const text = side === "left" ? row.left_text : row.right_text;
    const syntaxSpans = side === "left" ? row.left_syntax : row.right_syntax;
    const hasNonWhitespaceTokenChanges = Boolean(
        tokens?.some((tok) => tok.changed && !tok.is_ws),
    );
    const hasWhitespaceOnlyChanges = Boolean(
        tokens?.length
        && tokens.some((tok) => tok.changed)
        && !hasNonWhitespaceTokenChanges,
    );

    renderSyntaxText(contentEl, text || " ", syntaxSpans);
    if (tokens && tokens.length > 0) {
        if (hasWhitespaceOnlyChanges) {
            rowEl.classList.add("whitespace-only-change");
            rowEl.title = "Leading whitespace changed";
            noEl.title = "Leading whitespace changed";
        }
        decorateTokenDiff(contentEl, tokens);
    }

    codeEl.append(contentEl);
    rowEl.append(noEl, codeEl);
    return rowEl;
}

function isChangedRowStatus(status) {
    return status === "insert" || status === "delete" || status === "replace";
}

function renderSideBySide(rows, leftLabel, rightLabel) {
    const wrapper = document.createElement("div");
    wrapper.className = "diff-grid";

    const leftPane = document.createElement("section");
    leftPane.className = "diff-pane";
    leftPane.innerHTML = `<div class="diff-pane-header">${escapeHtml(leftLabel)}</div>`;
    const leftLines = document.createElement("div");
    leftLines.className = "diff-lines";

    const rightPane = document.createElement("section");
    rightPane.className = "diff-pane";
    rightPane.innerHTML = `<div class="diff-pane-header">${escapeHtml(rightLabel)}</div>`;
    const rightLines = document.createElement("div");
    rightLines.className = "diff-lines";

    const scheduleRowSync = makeScheduledRowSync(leftLines, rightLines);

    rows.forEach((row, index) => {
        const previous = index > 0 ? rows[index - 1] : null;
        const markHunkAnchor =
            isChangedRowStatus(row.status)
            && !isChangedRowStatus(previous?.status ?? "equal");

        leftLines.append(makeDiffRow(row, "left", markHunkAnchor));
        rightLines.append(makeDiffRow(row, "right"));
    });

    queueMicrotask(scheduleRowSync);

    leftPane.append(leftLines);
    rightPane.append(rightLines);
    wrapper.append(leftPane, rightPane);
    return wrapper;
}

function badge(text, className) {
    const node = document.createElement("span");
    node.className = `badge ${className}`;
    node.textContent = text;
    return node;
}

function makeFileCard(payload) {
    const card = document.createElement("article");
    card.className = "file-card";

    const title = document.createElement("h2");
    title.className = "file-title";
    title.textContent = payload.display_name;

    const subtitle = document.createElement("p");
    subtitle.className = "file-subtitle";
    subtitle.textContent =
        payload.mode === "git"
            ? payload.change_type === "rename"
                ? "Git-backed rename"
                : payload.change_type === "copy"
                ? "Git-backed copy"
                : "Git-backed file diff"
            : "Direct file-to-file diff";

    const titleWrap = document.createElement("div");
    titleWrap.append(title, subtitle);

    const badges = document.createElement("div");
    badges.className = "badge-row";
    const badgeNodes = [
        badge(payload.summary.left_exists ? "left exists" : "left missing", "badge-neutral"),
        badge(payload.summary.right_exists ? "right exists" : "right missing", "badge-neutral"),
    ];
    if (payload.change_type === "rename") {
        badgeNodes.push(badge("renamed", "badge-neutral"));
    } else if (payload.change_type === "copy") {
        badgeNodes.push(badge("copied", "badge-neutral"));
    }
    badges.append(...badgeNodes);

    const header = document.createElement("div");
    header.className = "file-card-header";
    header.append(titleWrap, badges);
    card.append(header);
    card.append(renderSideBySide(payload.rows, payload.left_label, payload.right_label));

    return card;
}

function makeErrorCard(entry) {
    const card = document.createElement("article");
    card.className = "file-card";

    const title = document.createElement("h2");
    title.className = "file-title";
    title.textContent = entry.display_name;

    const subtitle = document.createElement("p");
    subtitle.className = "file-subtitle";
    subtitle.textContent = entry.error || "Unable to render diff.";

    card.append(title, subtitle);
    return card;
}

function renderResult(payload) {
    resultPanel.replaceChildren();

    if (payload.mode === "repo") {
        if (!payload.files.length) {
            const box = document.createElement("div");
            box.className = "error-state";
            box.textContent = "No changed files for the selected sides.";
            resultPanel.append(box);
            return;
        }

        payload.files.forEach((entry) => {
            resultPanel.append(entry.error ? makeErrorCard(entry) : makeFileCard(entry));
        });
        return;
    }

    resultPanel.append(makeFileCard(payload));
}

function resetHunkNavState() {
    hunkNavState.activeIndex = null;
    hunkNavState.signature = "";
    hunkNavState.lastNavAt = 0;
}

function positionsSignature(positions) {
    return positions.join("|");
}

function shouldUseActiveHunkIndex(positions, viewportCenter) {
    if (!Number.isInteger(hunkNavState.activeIndex)) {
        return false;
    }
    if (hunkNavState.activeIndex < 0 || hunkNavState.activeIndex >= positions.length) {
        return false;
    }
    if (hunkNavState.signature !== positionsSignature(positions)) {
        return false;
    }

    if (Date.now() - hunkNavState.lastNavAt < 900) {
        return true;
    }

    const activePosition = positions[hunkNavState.activeIndex];
    return Math.abs(activePosition - viewportCenter) <= 24;
}

function isVisibleHunkAnchor(row) {
    return !!row && row.offsetParent !== null && row.getClientRects().length > 0;
}

function getVisibleHunkRows() {
    return Array.from(document.querySelectorAll(".diff-row.hunk-anchor"))
        .filter(isVisibleHunkAnchor);
}

function navigateHunk(direction) {
    const rows = getVisibleHunkRows();
    if (!rows.length) return false;

    const positions = window.fileDiffNav.uniqueSortedPositions(
        rows.map((row) => row.getBoundingClientRect().top + window.scrollY),
    );
    if (!positions.length) return false;

    const viewportCenter = window.scrollY + window.innerHeight / 2;
    const targetIndex = shouldUseActiveHunkIndex(positions, viewportCenter)
        ? window.fileDiffNav.stepHunkIndex(
            hunkNavState.activeIndex,
            direction,
            positions.length,
        )
        : window.fileDiffNav.pickTargetIndex(
            positions,
            viewportCenter,
            direction,
        );
    if (targetIndex === null) return false;

    hunkNavState.activeIndex = targetIndex;
    hunkNavState.signature = positionsSignature(positions);
    hunkNavState.lastNavAt = Date.now();

    window.scrollTo({
        top: Math.max(positions[targetIndex] - 120, 0),
        behavior: "smooth",
    });
    return true;
}

async function loadDiff() {
    const params = new URLSearchParams();
    const state = getControlState();
    if (!state.valid) {
        summaryGrid.replaceChildren();
        resultPanel.replaceChildren();
        setStatus(state.message, false);
        return;
    }

    params.set("mode", state.mode);
    params.set("left", state.left);
    params.set("right", state.right);
    if (state.path) {
        params.set("path", state.path);
    }
    if (state.leftFile) {
        params.set("left_file", state.leftFile);
    }
    if (state.rightFile) {
        params.set("right_file", state.rightFile);
    }

    history.replaceState({}, "", `/?${params.toString()}`);
    setStatus("Loading diff…");
    resetHunkNavState();
    const loadToken = ++activeLoadToken;

    try {
        const response = await fetch(`/api/diff?${params.toString()}`);
        const payload = await response.json();
        if (loadToken !== activeLoadToken) {
            return;
        }
        if (!response.ok) {
            throw new Error(payload.error || "Failed to load diff.");
        }

        renderSummary(payload.summary, payload.mode);
        renderResult(payload);
        setStatus(buildStatusMessage(state, payload));
    } catch (error) {
        if (loadToken !== activeLoadToken) {
            return;
        }
        summaryGrid.replaceChildren();
        resultPanel.replaceChildren();

        const box = document.createElement("div");
        box.className = "error-state";
        box.textContent = error.message;
        resultPanel.append(box);
        setStatus(error.message, true);
    }
}

function scheduleLoadDiff(delayMs = 180) {
    if (pendingLoadTimer) {
        clearTimeout(pendingLoadTimer);
    }
    pendingLoadTimer = window.setTimeout(() => {
        pendingLoadTimer = 0;
        loadDiff();
    }, delayMs);
}

function getControlState() {
    const mode = modeSelect.value;
    const path = pathInput.value.trim();
    const leftFile = leftFileInput.value.trim();
    const rightFile = rightFileInput.value.trim();

    if (leftFile || rightFile) {
        return {
            valid: true,
            mode: "direct-files",
            path,
            left: "index",
            right: "worktree",
            leftFile,
            rightFile,
        };
    }

    if (mode === "refs") {
        const left = leftRefInput.value.trim();
        const right = rightRefInput.value.trim();
        if (!left || !right) {
            return {
                valid: false,
                message: "Enter both refs to compare them.",
            };
        }
        return {
            valid: true,
            mode,
            path,
            left,
            right,
            leftFile: "",
            rightFile: "",
        };
    }

    const [left, right] = MODE_TO_SIDES[mode] || MODE_TO_SIDES.files;
    return {
        valid: true,
        mode,
        path,
        left,
        right,
        leftFile: "",
        rightFile: "",
    };
}

function buildStatusMessage(state, payload) {
    if (state.mode === "direct-files") {
        return payload.display_name;
    }
    if (state.mode === "files") {
        return state.path
            ? `Unstaged changes for ${state.path}`
            : "Unstaged changes in working tree";
    }
    if (state.mode === "staged") {
        return state.path
            ? `Staged changes for ${state.path}`
            : "Staged changes ready to commit";
    }
    if (state.mode === "against-head") {
        return state.path
            ? `Working tree vs HEAD for ${state.path}`
            : "Working tree vs HEAD";
    }
    return `${payload.left_label} vs ${payload.right_label}`;
}

function syncModeUI() {
    const customMode = modeSelect.value === "refs";
    customRefsGroup.hidden = !customMode;
}

prevHunkBtn.addEventListener("click", () => navigateHunk("prev"));
nextHunkBtn.addEventListener("click", () => navigateHunk("next"));

window.addEventListener("keydown", (event) => {
    if (event.key === "n" && !event.shiftKey) {
        navigateHunk("next");
    } else if (event.key === "N") {
        navigateHunk("prev");
    }
});

const search = new URLSearchParams(window.location.search);
const defaults = window.FILE_DIFF_DEFAULTS || {};

pathInput.value = search.get("path") || defaults.path || "";
leftFileInput.value = search.get("left_file") || defaults.left_file || "";
rightFileInput.value = search.get("right_file") || defaults.right_file || "";
const initialLeft = search.get("left") || defaults.left || "index";
const initialRight = search.get("right") || defaults.right || "worktree";
const initialMode = search.get("mode")
    || inferMode(initialLeft, initialRight)
    || "files";
modeSelect.value = initialMode;
if (initialMode === "refs") {
    leftRefInput.value = initialLeft;
    rightRefInput.value = initialRight;
}
if (leftFileInput.value || rightFileInput.value) {
    directFilesPanel.open = true;
}
syncModeUI();

form.addEventListener("submit", (event) => {
    event.preventDefault();
});
directFilesForm.addEventListener("submit", (event) => {
    event.preventDefault();
});

modeSelect.addEventListener("change", () => {
    syncModeUI();
    scheduleLoadDiff(0);
});
pathInput.addEventListener("input", () => scheduleLoadDiff());
leftRefInput.addEventListener("input", () => scheduleLoadDiff());
rightRefInput.addEventListener("input", () => scheduleLoadDiff());
leftFileInput.addEventListener("input", () => {
    if (leftFileInput.value.trim() || rightFileInput.value.trim()) {
        directFilesPanel.open = true;
    }
    scheduleLoadDiff();
});
rightFileInput.addEventListener("input", () => {
    if (leftFileInput.value.trim() || rightFileInput.value.trim()) {
        directFilesPanel.open = true;
    }
    scheduleLoadDiff();
});

if (defaults.repo_available || pathInput.value || leftFileInput.value || rightFileInput.value) {
    loadDiff();
} else {
    setStatus("Open this inside a Git repo, or use direct file diff.");
}

function inferMode(left, right) {
    if (!left || !right) {
        return null;
    }
    if (left === "index" && right === "worktree") {
        return "files";
    }
    if (left === "head" && right === "index") {
        return "staged";
    }
    if (left === "head" && right === "worktree") {
        return "against-head";
    }
    if (BUILTIN_SIDES.has(left) && BUILTIN_SIDES.has(right)) {
        return null;
    }
    return "refs";
}

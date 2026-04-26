const form = document.getElementById("controlsForm");
const modeSelect = document.getElementById("modeSelect");
const modeButtonRow = document.getElementById("modeButtonRow");
const modeHint = document.getElementById("modeHint");
const branchReviewGroup = document.getElementById("branchReviewGroup");
const baseBranchInput = document.getElementById("baseBranchInput");
const branchInput = document.getElementById("branchInput");
const customRefsGroup = document.getElementById("customRefsGroup");
const leftRefInput = document.getElementById("leftRefInput");
const rightRefInput = document.getElementById("rightRefInput");
const statusText = document.getElementById("statusText");
const summaryGrid = document.getElementById("summaryGrid");
const resultPanel = document.getElementById("resultPanel");
const prevHunkBtn = document.getElementById("prevHunkBtn");
const nextHunkBtn = document.getElementById("nextHunkBtn");

const rowSyncApi = window.fileDiffRowSync || {};
const foldApi = window.fileDiffFolds || {};
const registeredRowSyncs = window.__fileDiffRowSyncHandlers
    || (window.__fileDiffRowSyncHandlers = new Set());
const BUILTIN_SIDES = new Set(["head", "index", "worktree"]);
const MODE_TO_SIDES = {
    files: ["index", "worktree"],
    staged: ["head", "index"],
    "against-head": ["head", "worktree"],
};
const MODE_HINTS = {
    files: "Show unstaged changes between the index and your working tree.",
    staged: "Show what is staged and ready to commit.",
    "against-head": "Show everything in your working tree compared with HEAD.",
    "branch-review": "Show changes on one branch since it split from your base branch.",
    refs: "Compare two exact refs directly without using a merge base.",
};
const REF_SECTION_LABELS = {
    builtins: "Built-in",
    locals: "Local branches",
    remotes: "Remote refs",
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

function filterRefChoices(refChoices, query, sections) {
    const needle = query.trim().toLowerCase();
    const filtered = [];
    for (const section of sections) {
        const values = (refChoices[section] || []).filter((value) => {
            if (!needle) {
                return true;
            }
            return value.toLowerCase().includes(needle);
        });
        if (values.length) {
            filtered.push([section, values]);
        }
    }
    return filtered;
}

function attachAutocomplete(input, refChoices, sections) {
    const host = input.closest("label");
    if (!host) {
        return;
    }
    host.classList.add("autocomplete-host");

    const panel = document.createElement("div");
    panel.className = "autocomplete-panel";
    panel.hidden = true;
    host.append(panel);

    let blurTimer = 0;

    const closePanel = () => {
        panel.hidden = true;
        panel.replaceChildren();
    };

    const openPanel = () => {
        const groups = filterRefChoices(refChoices, input.value, sections);
        panel.replaceChildren();
        if (!groups.length) {
            closePanel();
            return;
        }

        for (const [section, values] of groups) {
            const sectionNode = document.createElement("div");
            sectionNode.className = "autocomplete-section";

            const labelNode = document.createElement("div");
            labelNode.className = "autocomplete-section-label";
            labelNode.textContent = REF_SECTION_LABELS[section] || section;
            sectionNode.append(labelNode);

            for (const value of values) {
                const option = document.createElement("button");
                option.type = "button";
                option.className = "autocomplete-option";
                option.textContent = value;
                option.addEventListener("mousedown", (event) => {
                    event.preventDefault();
                    input.value = value;
                    closePanel();
                    input.dispatchEvent(new Event("input", { bubbles: true }));
                    input.focus();
                });
                sectionNode.append(option);
            }
            panel.append(sectionNode);
        }

        panel.hidden = false;
    };

    input.addEventListener("focus", openPanel);
    input.addEventListener("input", openPanel);
    input.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closePanel();
        }
    });
    input.addEventListener("blur", () => {
        blurTimer = window.setTimeout(closePanel, 120);
    });
    panel.addEventListener("mousedown", () => {
        if (blurTimer) {
            clearTimeout(blurTimer);
        }
    });
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

function makeDiffRow(row, side, markHunkAnchor = false, hunkIndex = null) {
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
    if (Number.isInteger(hunkIndex)) {
        rowEl.dataset.hunkIndex = String(hunkIndex);
        rowEl.classList.add("hunk-anchor-row");
        const rows = hunkRowsByIndex.get(hunkIndex) || [];
        rows.push(rowEl);
        hunkRowsByIndex.set(hunkIndex, rows);
    }
    if (markHunkAnchor) {
        hunkAnchorRows.push(rowEl);
    }

    const noEl = document.createElement("div");
    noEl.className = "line-no";
    const noValue = document.createElement("span");
    noValue.className = "line-no-value";
    noValue.textContent = (side === "left" ? row.left_no : row.right_no) ?? "";
    noEl.append(noValue);

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

function makeFoldBar(count, label = "") {
    const bar = document.createElement("div");
    bar.className = "diff-row fold-bar";
    let text = `... ${count} line${count !== 1 ? "s" : ""}`;
    if (label) {
        text = `... ${count} line${count !== 1 ? "s" : ""} in ${label}`;
    }
    bar.innerHTML = `<div class="line-no">..</div><div class="fold-label">${escapeHtml(text)}</div>`;
    return bar;
}

function makeInlineFoldToggle(onClick) {
    const icon = document.createElement("span");
    icon.className = "inline-fold-toggle";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "▸";
    icon.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        onClick();
    });
    return icon;
}

function setInlineFoldState(signatureRow, expanded) {
    signatureRow.classList.toggle("fold-expanded", expanded);
}

function renderSideBySide(rows, leftLabel, rightLabel, startHunkIndex = 0, foldHints = []) {
    const processedRows = foldApi.addFoldRows ? foldApi.addFoldRows(rows, foldHints) : rows;
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
    let nextHunkIndex = startHunkIndex;

    processedRows.forEach((row, index) => {
        if (row.status === "fold") {
            const leftBar = makeFoldBar(row.count, row.label);
            const rightBar = makeFoldBar(row.count, row.label);
            const leftExpandedRows = [];
            const rightExpandedRows = [];
            const leftSignatureRow = leftLines.lastElementChild;
            const rightSignatureRow = rightLines.lastElementChild;
            const leftBarAnchor = document.createComment("fold-bar-anchor");
            const rightBarAnchor = document.createComment("fold-bar-anchor");
            let expanded = false;

            leftLines.append(leftBar);
            leftLines.append(leftBarAnchor);
            rightLines.append(rightBar);
            rightLines.append(rightBarAnchor);

            if (!leftSignatureRow || !rightSignatureRow) {
                return;
            }

            const leftNo = leftSignatureRow.querySelector(".line-no");
            const rightNo = rightSignatureRow.querySelector(".line-no");
            if (!leftNo || !rightNo) {
                return;
            }

            const leftToggleIcon = makeInlineFoldToggle(toggleFold);
            const rightToggleIcon = makeInlineFoldToggle(toggleFold);

            leftNo.prepend(leftToggleIcon);
            rightNo.prepend(rightToggleIcon);

            leftSignatureRow.classList.add("fold-toggle-row");
            rightSignatureRow.classList.add("fold-toggle-row");
            leftSignatureRow.title = "Toggle fold";
            rightSignatureRow.title = "Toggle fold";

            setInlineFoldState(leftSignatureRow, false);
            setInlineFoldState(rightSignatureRow, false);

            function toggleFold() {
                expanded = !expanded;
                if (expanded) {
                    row.foldedRows.forEach((foldedRow) => {
                        const leftNode = makeDiffRow(foldedRow, "left");
                        const rightNode = makeDiffRow(foldedRow, "right");
                        leftExpandedRows.push(leftNode);
                        rightExpandedRows.push(rightNode);
                        leftLines.insertBefore(leftNode, leftBarAnchor);
                        rightLines.insertBefore(rightNode, rightBarAnchor);
                    });
                    leftBar.remove();
                    rightBar.remove();
                    setInlineFoldState(leftSignatureRow, true);
                    setInlineFoldState(rightSignatureRow, true);
                    queueMicrotask(scheduleRowSync);
                    return;
                }

                leftExpandedRows.splice(0).forEach((node) => node.remove());
                rightExpandedRows.splice(0).forEach((node) => node.remove());
                leftLines.insertBefore(leftBar, leftBarAnchor);
                rightLines.insertBefore(rightBar, rightBarAnchor);
                setInlineFoldState(leftSignatureRow, false);
                setInlineFoldState(rightSignatureRow, false);
                scheduleRowSync();
            }

            leftBar.addEventListener("click", toggleFold);
            rightBar.addEventListener("click", toggleFold);
            leftSignatureRow.addEventListener("click", toggleFold);
            rightSignatureRow.addEventListener("click", toggleFold);
            return;
        }

        const previous = index > 0 ? processedRows[index - 1] : null;
        const markHunkAnchor =
            isChangedRowStatus(row.status)
            && !isChangedRowStatus(previous?.status ?? "equal");
        const anchorIndex = markHunkAnchor ? nextHunkIndex++ : null;

        leftLines.append(makeDiffRow(row, "left", markHunkAnchor, anchorIndex));
        rightLines.append(makeDiffRow(row, "right", false, anchorIndex));
    });

    queueMicrotask(scheduleRowSync);

    leftPane.append(leftLines);
    rightPane.append(rightLines);
    wrapper.append(leftPane, rightPane);
    return {
        wrapper,
        nextHunkIndex,
    };
}

function badge(text, className) {
    const node = document.createElement("span");
    node.className = `badge ${className}`;
    node.textContent = text;
    return node;
}

function makeFileCard(payload, startHunkIndex = 0) {
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
            : "Git-backed file diff";

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
    const { wrapper, nextHunkIndex } = renderSideBySide(
        payload.rows,
        payload.left_label,
        payload.right_label,
        startHunkIndex,
        payload.fold_hints || [],
    );
    card.append(wrapper);

    return {
        card,
        nextHunkIndex,
    };
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
    resetHunkCaches();
    resultPanel.replaceChildren();

    if (payload.mode === "repo") {
        if (!payload.files.length) {
            const box = document.createElement("div");
            box.className = "error-state";
            box.textContent = "No changed files for the selected sides.";
            resultPanel.append(box);
            return;
        }

        let nextHunkIndex = 0;
        payload.files.forEach((entry) => {
            if (entry.error) {
                resultPanel.append(makeErrorCard(entry));
                return;
            }
            const result = makeFileCard(entry, nextHunkIndex);
            nextHunkIndex = result.nextHunkIndex;
            resultPanel.append(result.card);
        });
        return;
    }

    resultPanel.append(makeFileCard(payload).card);
}

function isVisibleHunkAnchor(row) {
    return !!row && row.offsetParent !== null && row.getClientRects().length > 0;
}

let hunkAnchorRows = [];
const hunkRowsByIndex = new Map();
let selectedHunkIndex = null;

function resetHunkCaches() {
    hunkAnchorRows = [];
    hunkRowsByIndex.clear();
    selectedHunkIndex = null;
}

function getVisibleHunkRows() {
    return hunkAnchorRows.filter(isVisibleHunkAnchor);
}

function readHunkNavSnapshot() {
    const positions = getVisibleHunkRows()
        .map((row) => row.getBoundingClientRect().top + window.scrollY);

    return {
        positions,
        scrollY: window.scrollY,
        maxScrollTop: Math.max(
            document.documentElement.scrollHeight - window.innerHeight,
            0,
        ),
    };
}

function setRowsSelected(rows, isActive) {
    for (const row of rows || []) {
        row.classList.toggle("active-hunk", isActive);
        row.setAttribute("aria-current", isActive ? "true" : "false");
    }
}

function syncSelectedHunk(index) {
    const nextIndex = Number.isInteger(index) ? index : null;
    if (selectedHunkIndex === nextIndex) {
        return;
    }

    if (selectedHunkIndex !== null) {
        setRowsSelected(hunkRowsByIndex.get(selectedHunkIndex), false);
    }
    if (nextIndex !== null) {
        setRowsSelected(hunkRowsByIndex.get(nextIndex), true);
    }

    selectedHunkIndex = nextIndex;
}

const hunkNavController = window.fileDiffNav.createHunkNavigationController({
    readSnapshot: readHunkNavSnapshot,
    scrollTo(top, behavior) {
        window.scrollTo({
            top,
            behavior,
        });
    },
    setTimeout: window.setTimeout.bind(window),
    clearTimeout: window.clearTimeout.bind(window),
}, {
    onActiveIndexChange: syncSelectedHunk,
});

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
    if (state.left) {
        params.set("left", state.left);
    }
    if (state.right) {
        params.set("right", state.right);
    }
    if (state.baseBranch) {
        params.set("base_branch", state.baseBranch);
    }
    if (state.branch) {
        params.set("branch", state.branch);
    }
    history.replaceState({}, "", `/?${params.toString()}`);
    setStatus("Loading diff…");
    hunkNavController.reset();
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
        syncSelectedHunk(null);
        setStatus(buildStatusMessage(state, payload));
    } catch (error) {
        if (loadToken !== activeLoadToken) {
            return;
        }
        summaryGrid.replaceChildren();
        resultPanel.replaceChildren();
        resetHunkCaches();
        syncSelectedHunk(null);

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
            left,
            right,
        };
    }

    if (mode === "branch-review") {
        const baseBranch = baseBranchInput.value.trim();
        const branch = branchInput.value.trim();
        if (!branch) {
            return {
                valid: false,
                message: "Pick a branch to compare against the base branch.",
            };
        }
        return {
            valid: true,
            mode,
            left: "",
            right: "",
            baseBranch,
            branch,
        };
    }

    const [left, right] = MODE_TO_SIDES[mode] || MODE_TO_SIDES.files;
    return {
        valid: true,
        mode,
        left,
        right,
        baseBranch: "",
        branch: "",
    };
}

function buildStatusMessage(state, payload) {
    if (state.mode === "files") {
        return "Unstaged changes in working tree";
    }
    if (state.mode === "staged") {
        return "Staged changes ready to commit";
    }
    if (state.mode === "against-head") {
        return "Working tree vs HEAD";
    }
    if (state.mode === "branch-review") {
        const baseBranch = state.baseBranch || "master";
        return `${state.branch} vs ${baseBranch}`;
    }
    return `${payload.left_label} vs ${payload.right_label}`;
}

function syncModeUI() {
    const mode = modeSelect.value;
    customRefsGroup.hidden = mode !== "refs";
    branchReviewGroup.hidden = mode !== "branch-review";
    modeHint.textContent = MODE_HINTS[mode] || "";

    for (const button of modeButtonRow.querySelectorAll(".mode-button")) {
        const isActive = button.dataset.mode === mode;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-selected", isActive ? "true" : "false");
    }
}

function shouldIgnoreHunkNavKeyEvent(event) {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) {
        return true;
    }

    const target = event.target;
    if (!(target instanceof HTMLElement)) {
        return false;
    }

    return target.isContentEditable
        || target.closest("input, textarea, select, [contenteditable='true']");
}

prevHunkBtn.addEventListener("click", () => hunkNavController.request("prev"));
nextHunkBtn.addEventListener("click", () => hunkNavController.request("next"));

window.addEventListener("keydown", (event) => {
    if (shouldIgnoreHunkNavKeyEvent(event)) {
        return;
    }
    if (event.key === "n" && !event.shiftKey) {
        hunkNavController.request("next");
    } else if (event.key === "N") {
        hunkNavController.request("prev");
    }
});
window.addEventListener(
    "scroll",
    () => {
        hunkNavController.handleScroll();
    },
    { passive: true },
);

const search = new URLSearchParams(window.location.search);
const defaults = window.FILE_DIFF_DEFAULTS || {};
const refChoices = defaults.ref_choices || {
    builtins: [],
    locals: [],
    remotes: [],
};

baseBranchInput.value = search.get("base_branch") || defaults.base_branch || "";
branchInput.value = search.get("branch") || defaults.branch || "";
const initialLeft = search.get("left") || defaults.left || "index";
const initialRight = search.get("right") || defaults.right || "worktree";
const initialMode = search.get("mode")
    || defaults.mode
    || inferMode(initialLeft, initialRight, branchInput.value, baseBranchInput.value)
    || "branch-review";
modeSelect.value = initialMode;
if (initialMode === "refs") {
    leftRefInput.value = initialLeft;
    rightRefInput.value = initialRight;
}
syncModeUI();
attachAutocomplete(baseBranchInput, refChoices, ["locals", "remotes"]);
attachAutocomplete(branchInput, refChoices, ["locals", "remotes"]);
attachAutocomplete(leftRefInput, refChoices, ["builtins", "locals", "remotes"]);
attachAutocomplete(rightRefInput, refChoices, ["builtins", "locals", "remotes"]);

form.addEventListener("submit", (event) => {
    event.preventDefault();
});

for (const button of modeButtonRow.querySelectorAll(".mode-button")) {
    button.addEventListener("click", () => {
        const nextMode = button.dataset.mode;
        if (!nextMode || modeSelect.value === nextMode) {
            return;
        }
        modeSelect.value = nextMode;
        syncModeUI();
        scheduleLoadDiff(0);
    });
}

modeSelect.addEventListener("change", () => {
    syncModeUI();
    scheduleLoadDiff(0);
});
baseBranchInput.addEventListener("input", () => scheduleLoadDiff());
branchInput.addEventListener("input", () => scheduleLoadDiff());
leftRefInput.addEventListener("input", () => scheduleLoadDiff());
rightRefInput.addEventListener("input", () => scheduleLoadDiff());

if (defaults.repo_available) {
    loadDiff();
} else {
    setStatus("Open this inside a Git repo.");
}

function inferMode(left, right) {
    if (branchInput.value || baseBranchInput.value) {
        return "branch-review";
    }
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

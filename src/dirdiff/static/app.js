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
const DEBUG_SCROLL_ENABLED = new URLSearchParams(window.location.search).get("debug_scroll") === "1";

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
const hunkNavState = {
    activeIndex: null,
    signature: "",
    lastNavAt: 0,
};
const hunkHoldState = {
    button: null,
    direction: null,
    startAt: 0,
    rafId: 0,
    emittedRepeats: 0,
};
const suppressedHunkClick = {
    button: null,
    until: 0,
};
const HUNK_HOLD_DELAY_MS = 320;
const HUNK_HOLD_SUPPRESS_CLICK_MS = 420;
const ROW_RENDER_BATCH_SIZE = 120;
const EAGER_ROW_DECORATION_LIMIT = 140;
const DECORATION_PREFETCH_MARGIN_PX = 600;
let pendingLoadTimer = 0;
let activeLoadToken = 0;
let activeDiffStream = null;
let activeRenderPass = 0;
let currentPayload = null;
let debugScrollLog = [];
let debugScrollPanel = null;
let debugScrollBody = null;
let lastScrollAt = 0;
let deferredRowDecorationObserver = null;
const deferredRowDecorations = new WeakMap();
const pendingRowDecorationTargets = new Set();
let rowDecorationFlushScheduled = false;

function copyDebugText(text) {
    if (navigator.clipboard?.writeText) {
        return navigator.clipboard.writeText(text);
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.top = "-9999px";
    document.body.append(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
    return Promise.resolve();
}

function formatDebugScrollLogEntry(entry) {
    return `${entry.t}ms [${entry.tag}] ${entry.message}`;
}

function updateDebugScrollPanel() {
    if (!debugScrollBody) {
        return;
    }
    debugScrollBody.textContent = debugScrollLog
        .map(formatDebugScrollLogEntry)
        .join("\n");
}

function appendDebugScrollLog(tag, message) {
    if (!DEBUG_SCROLL_ENABLED) {
        return;
    }
    const entry = {
        t: Math.round(performance.now()),
        tag,
        message,
    };
    debugScrollLog.push(entry);
    if (debugScrollLog.length > 250) {
        debugScrollLog = debugScrollLog.slice(-250);
    }
    console.log(`[scroll-debug][${tag}] ${message}`);
    updateDebugScrollPanel();
}

function mountDebugScrollPanel() {
    if (!DEBUG_SCROLL_ENABLED || debugScrollPanel) {
        return;
    }
    debugScrollPanel = document.createElement("aside");
    debugScrollPanel.className = "scroll-debug-panel";

    const header = document.createElement("div");
    header.className = "scroll-debug-header";

    const title = document.createElement("strong");
    title.textContent = "Scroll debug";

    const actions = document.createElement("div");
    actions.className = "scroll-debug-actions";

    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = "scroll-debug-copy";
    copyButton.textContent = "Copy debug log";
    copyButton.addEventListener("click", async () => {
        const text = debugScrollLog.map(formatDebugScrollLogEntry).join("\n");
        await copyDebugText(text);
        appendDebugScrollLog("debug", "Copied debug log");
    });

    const clearButton = document.createElement("button");
    clearButton.type = "button";
    clearButton.className = "scroll-debug-clear";
    clearButton.textContent = "Clear";
    clearButton.addEventListener("click", () => {
        debugScrollLog = [];
        updateDebugScrollPanel();
        appendDebugScrollLog("debug", "Cleared log");
    });

    actions.append(copyButton, clearButton);
    header.append(title, actions);

    debugScrollBody = document.createElement("pre");
    debugScrollBody.className = "scroll-debug-log";

    debugScrollPanel.append(header, debugScrollBody);
    document.body.append(debugScrollPanel);
    appendDebugScrollLog("debug", `Mounted panel at ${window.location.search || "?"}`);
}

function setForceParam(params, force) {
    if (force) {
        params.set("force", "1");
    } else {
        params.delete("force");
    }
}

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
            appendDebugScrollLog("rowSync", "Skipped run for detached diff panes");
            unregister();
            return;
        }
        const msSinceScroll = performance.now() - lastScrollAt;
        if (msSinceScroll < 140) {
            appendDebugScrollLog("rowSync", `Deferred sync during active scroll (${Math.round(msSinceScroll)}ms)`);
            frameId = requestAnimationFrame(runSync);
            return;
        }
        const leftRows = rowSyncApi.collectDirectDiffRows?.(leftLines).length ?? 0;
        const rightRows = rowSyncApi.collectDirectDiffRows?.(rightLines).length ?? 0;
        appendDebugScrollLog("rowSync", `Running sync for ${leftRows}/${rightRows} rows at scrollY=${Math.round(window.scrollY)}`);
        rowSyncApi.syncDiffRowHeights?.(leftLines, rightLines);
    };

    unregister = registerRowSync(runSync);

    return () => {
        if (frameId) {
            cancelAnimationFrame(frameId);
        }
        appendDebugScrollLog("rowSync", `Scheduled sync at scrollY=${Math.round(window.scrollY)}`);
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

function setHunkHoldVisual(button, progress, isRepeating) {
    if (!button) return;
    button.style.setProperty("--hold-progress", String(progress));
    button.classList.toggle("is-hold-tracking", progress > 0);
    button.classList.toggle("is-hold-repeating", isRepeating);
}

function clearHunkHoldVisual(button) {
    if (!button) return;
    button.style.removeProperty("--hold-progress");
    button.classList.remove("is-hold-tracking", "is-hold-repeating");
}

function markHunkClickSuppressed(button) {
    if (!button) return;
    button.dataset.suppressHoldClick = "true";
}

function clearSuppressedHunkClick(button) {
    if (!button) return;
    delete button.dataset.suppressHoldClick;
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

function yieldToBrowser() {
    return new Promise((resolve) => {
        requestAnimationFrame(() => resolve());
    });
}

function ensureDeferredRowDecorationObserver() {
    if (deferredRowDecorationObserver || typeof IntersectionObserver === "undefined") {
        return deferredRowDecorationObserver;
    }
    deferredRowDecorationObserver = new IntersectionObserver(
        (entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) {
                    continue;
                }
                const renderDeferred = deferredRowDecorations.get(entry.target);
                if (!renderDeferred) {
                    continue;
                }
                deferredRowDecorationObserver.unobserve(entry.target);
                pendingRowDecorationTargets.add(entry.target);
                schedulePendingRowDecorationFlush();
            }
        },
        {
            rootMargin: `${DECORATION_PREFETCH_MARGIN_PX}px 0px`,
        },
    );
    return deferredRowDecorationObserver;
}

function applyRowDecoration(codeEl, text, syntaxSpans, tokens) {
    codeEl.replaceChildren();
    renderSyntaxText(codeEl, text || " ", syntaxSpans);
    if (tokens && tokens.length > 0) {
        decorateTokenDiff(codeEl, tokens);
    }
}

function renderRowPlainText(codeEl, text) {
    codeEl.textContent = text || " ";
}

function queueDeferredRowDecoration(codeEl, text, syntaxSpans, tokens) {
    renderRowPlainText(codeEl, text);
    if ((!syntaxSpans || !syntaxSpans.length) && (!tokens || !tokens.length)) {
        return;
    }

    const renderDeferred = () => {
        applyRowDecoration(codeEl, text, syntaxSpans, tokens);
    };

    deferredRowDecorations.set(codeEl, renderDeferred);
    const observer = ensureDeferredRowDecorationObserver();
    if (observer) {
        observer.observe(codeEl);
        return;
    }

    window.setTimeout(() => {
        if (deferredRowDecorations.get(codeEl) !== renderDeferred) {
            return;
        }
        deferredRowDecorations.delete(codeEl);
        renderDeferred();
    }, 0);
}

function flushPendingRowDecorations(deadline = null) {
    rowDecorationFlushScheduled = false;
    let processed = 0;
    const budgeted = deadline && typeof deadline.timeRemaining === "function";

    while (pendingRowDecorationTargets.size) {
        const nextTarget = pendingRowDecorationTargets.values().next().value;
        pendingRowDecorationTargets.delete(nextTarget);
        const renderDeferred = deferredRowDecorations.get(nextTarget);
        if (!renderDeferred) {
            continue;
        }
        deferredRowDecorations.delete(nextTarget);
        renderDeferred();
        processed += 1;

        if (budgeted) {
            if (deadline.timeRemaining() < 4 && processed >= 1) {
                break;
            }
        } else if (processed >= 4) {
            break;
        }
    }

    if (pendingRowDecorationTargets.size) {
        schedulePendingRowDecorationFlush();
    }
}

function schedulePendingRowDecorationFlush() {
    if (rowDecorationFlushScheduled) {
        return;
    }
    rowDecorationFlushScheduled = true;
    if (typeof window.requestIdleCallback === "function") {
        window.requestIdleCallback(flushPendingRowDecorations, { timeout: 120 });
        return;
    }
    requestAnimationFrame(() => {
        flushPendingRowDecorations();
    });
}

function makeDiffRow(
    row,
    side,
    markHunkAnchor = false,
    hunkIndex = null,
    { deferDecoration = false } = {},
) {
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
    noEl.textContent = (side === "left" ? row.left_no : row.right_no) ?? "";

    const codeEl = document.createElement("code");
    codeEl.className = "line-code";

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

    if (hasWhitespaceOnlyChanges) {
        rowEl.classList.add("whitespace-only-change");
        rowEl.title = "Leading whitespace changed";
        noEl.title = "Leading whitespace changed";
    }

    if (deferDecoration) {
        queueDeferredRowDecoration(codeEl, text || " ", syntaxSpans, tokens);
    } else {
        applyRowDecoration(codeEl, text || " ", syntaxSpans, tokens);
    }

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

function countHunkAnchors(rows) {
    let total = 0;
    rows.forEach((row, index) => {
        if (row.status === "fold" || row.status === "elided") {
            return;
        }
        const previous = index > 0 ? rows[index - 1] : null;
        if (
            isChangedRowStatus(row.status)
            && !isChangedRowStatus(previous?.status ?? "equal")
        ) {
            total += 1;
        }
    });
    return total;
}

async function appendRenderedRowsInBatches(
    processedRows,
    leftLines,
    rightLines,
    scheduleRowSync,
    startHunkIndex,
    renderPassId,
) {
    let nextHunkIndex = startHunkIndex;
    let renderIndex = 0;
    let cursor = 0;
    let lastLeftRow = leftLines.lastElementChild;
    let lastRightRow = rightLines.lastElementChild;

    while (cursor < processedRows.length) {
        if (renderPassId !== activeRenderPass) {
            return;
        }

        const leftFragment = document.createDocumentFragment();
        const rightFragment = document.createDocumentFragment();
        const batchEnd = Math.min(cursor + ROW_RENDER_BATCH_SIZE, processedRows.length);

        for (; cursor < batchEnd; cursor += 1) {
            const row = processedRows[cursor];

            if (row.status === "elided") {
                const leftBar = makeFoldBar(row.count, row.label);
                const rightBar = makeFoldBar(row.count, row.label);
                leftFragment.append(leftBar);
                rightFragment.append(rightBar);
                lastLeftRow = leftBar;
                lastRightRow = rightBar;
                continue;
            }

            if (row.status === "fold") {
                const leftBar = makeFoldBar(row.count, row.label);
                const rightBar = makeFoldBar(row.count, row.label);
                const leftExpandedRows = [];
                const rightExpandedRows = [];
                const leftSignatureRow = lastLeftRow;
                const rightSignatureRow = lastRightRow;
                const leftBarAnchor = document.createComment("fold-bar-anchor");
                const rightBarAnchor = document.createComment("fold-bar-anchor");
                let expanded = false;

                leftFragment.append(leftBar);
                leftFragment.append(leftBarAnchor);
                rightFragment.append(rightBar);
                rightFragment.append(rightBarAnchor);
                lastLeftRow = leftBar;
                lastRightRow = rightBar;

                if (!leftSignatureRow || !rightSignatureRow) {
                    continue;
                }

                const leftNo = leftSignatureRow.querySelector(".line-no");
                const rightNo = rightSignatureRow.querySelector(".line-no");
                if (!leftNo || !rightNo) {
                    continue;
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
                            const leftNode = makeDiffRow(
                                foldedRow,
                                "left",
                                false,
                                null,
                                { deferDecoration: false },
                            );
                            const rightNode = makeDiffRow(
                                foldedRow,
                                "right",
                                false,
                                null,
                                { deferDecoration: false },
                            );
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
                continue;
            }

            const previous = cursor > 0 ? processedRows[cursor - 1] : null;
            const markHunkAnchor =
                isChangedRowStatus(row.status)
                && !isChangedRowStatus(previous?.status ?? "equal");
            const anchorIndex = markHunkAnchor ? nextHunkIndex++ : null;
            const deferDecoration =
                row.status === "equal" && renderIndex >= EAGER_ROW_DECORATION_LIMIT;
            const leftNode = makeDiffRow(
                row,
                "left",
                markHunkAnchor,
                anchorIndex,
                { deferDecoration },
            );
            const rightNode = makeDiffRow(
                row,
                "right",
                false,
                anchorIndex,
                { deferDecoration },
            );

            leftFragment.append(leftNode);
            rightFragment.append(rightNode);
            lastLeftRow = leftNode;
            lastRightRow = rightNode;
            renderIndex += 1;
        }

        leftLines.append(leftFragment);
        rightLines.append(rightFragment);

        if (cursor < processedRows.length) {
            await yieldToBrowser();
        }
    }

    queueMicrotask(scheduleRowSync);
}

function renderSideBySide(
    rows,
    leftLabel,
    rightLabel,
    startHunkIndex = 0,
    foldHints = [],
    renderPassId = activeRenderPass,
) {
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
    const nextHunkIndex = startHunkIndex + countHunkAnchors(processedRows);

    leftPane.append(leftLines);
    rightPane.append(rightLines);
    wrapper.append(leftPane, rightPane);
    const renderPromise = appendRenderedRowsInBatches(
        processedRows,
        leftLines,
        rightLines,
        scheduleRowSync,
        startHunkIndex,
        renderPassId,
    );
    return {
        wrapper,
        nextHunkIndex,
        renderPromise,
    };
}

function badge(text, className) {
    const node = document.createElement("span");
    node.className = `badge ${className}`;
    node.textContent = text;
    return node;
}

function makeFileCard(payload, startHunkIndex = 0, renderPassId = activeRenderPass) {
    if (payload.lazy_load) {
        return {
            card: makeLazyFileCard(payload),
            nextHunkIndex: startHunkIndex,
            renderPromise: Promise.resolve(),
        };
    }

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
    if (payload.render_mode === "plain") {
        badgeNodes.push(badge("plain render", "badge-neutral"));
    }
    if (payload.truncated_rows) {
        badgeNodes.push(badge(`truncated ${payload.truncated_rows}`, "badge-neutral"));
    }
    badges.append(...badgeNodes);

    const header = document.createElement("div");
    header.className = "file-card-header";
    header.append(titleWrap, badges);
    card.append(header);
    const { wrapper, nextHunkIndex, renderPromise } = renderSideBySide(
        payload.rows,
        payload.left_label,
        payload.right_label,
        startHunkIndex,
        payload.fold_hints || [],
        renderPassId,
    );
    card.append(wrapper);

    return {
        card,
        nextHunkIndex,
        renderPromise,
    };
}

function makeLazyFileCard(payload) {
    const card = document.createElement("article");
    card.className = "file-card";

    const title = document.createElement("h2");
    title.className = "file-title";
    title.textContent = payload.display_name;

    const subtitle = document.createElement("p");
    subtitle.className = "file-subtitle";
    subtitle.textContent = "Notebook diff skipped by default. Load it explicitly if you want to inspect it here.";

    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Load diff";
    button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = "Loading…";
        try {
            const loaded = await fetchFileDiff(payload);
            replaceLazyEntry(payload, loaded);
        } catch (error) {
            button.disabled = false;
            button.textContent = "Load diff";
            subtitle.textContent = error.message;
        }
    });

    card.append(title, subtitle, button);
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
    currentPayload = payload;
    resetHunkCaches();
    resultPanel.replaceChildren();
    const renderPassId = ++activeRenderPass;

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
            const result = makeFileCard(entry, nextHunkIndex, renderPassId);
            nextHunkIndex = result.nextHunkIndex;
            resultPanel.append(result.card);
        });
        return;
    }

    resultPanel.append(makeFileCard(payload, 0, renderPassId).card);
}

function closeActiveDiffStream() {
    if (!activeDiffStream) {
        return;
    }
    activeDiffStream.close();
    activeDiffStream = null;
}

async function fetchFileDiff(entry) {
    const params = new URLSearchParams();
    const state = getControlState();
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
    if (entry.left_path) {
        params.set("left_path", entry.left_path);
    }
    if (entry.right_path) {
        params.set("right_path", entry.right_path);
    }
    params.set("display_name", entry.display_name);
    params.set("change_type", entry.change_type || "modify");

    const response = await fetch(`/api/file-diff?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.error || "Failed to load file diff.");
    }
    return payload;
}

function replaceLazyEntry(previousEntry, nextEntry) {
    if (!currentPayload || currentPayload.mode !== "repo") {
        return;
    }
    currentPayload.files = currentPayload.files.map((entry) => (
        entry === previousEntry ? nextEntry : entry
    ));
    renderSummary(currentPayload.summary, currentPayload.mode);
    renderResult(currentPayload);
}

function shouldStreamDiff(state) {
    return state.mode !== "refs" && typeof EventSource !== "undefined";
}

function beginRepoStream(initialPayload) {
    const renderPassId = ++activeRenderPass;
    resetHunkCaches();
    resultPanel.replaceChildren();
    renderSummary(initialPayload.summary, initialPayload.mode);
    syncSelectedHunk(null);

    const payload = {
            ...initialPayload,
            files: [],
    };
    currentPayload = payload;
    return {
        payload,
        nextHunkIndex: 0,
        renderPassId,
    };
}

function appendRepoStreamEntry(streamState, entry, summary) {
    streamState.payload.summary = summary;
    streamState.payload.files.push(entry);
    currentPayload = streamState.payload;
    renderSummary(summary, streamState.payload.mode);

    if (entry.error) {
        resultPanel.append(makeErrorCard(entry));
        return;
    }

    const result = makeFileCard(entry, streamState.nextHunkIndex, streamState.renderPassId);
    streamState.nextHunkIndex = result.nextHunkIndex;
    resultPanel.append(result.card);
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
    hunkNavState.activeIndex = null;
    hunkNavState.signature = "";
    hunkNavState.lastNavAt = 0;
}

function getVisibleHunkRows() {
    let rows = hunkAnchorRows.filter(isVisibleHunkAnchor);
    if (!rows.length) {
        rows = Array.from(document.querySelectorAll(".hunk-anchor"))
            .filter(isVisibleHunkAnchor);
    }
    return rows;
}

function positionsSignature(positions) {
    return positions.join("|");
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

function stepHunkIndexWithoutWrap(currentIndex, direction, length) {
    if (!Number.isInteger(currentIndex) || length <= 0) {
        return null;
    }
    if (direction === "next") {
        return currentIndex + 1 < length ? currentIndex + 1 : null;
    }
    return currentIndex - 1 >= 0 ? currentIndex - 1 : null;
}

function pickTargetIndexWithoutWrap(positions, viewportCenter, direction) {
    if (!positions.length) {
        return null;
    }

    const firstPosition = positions[0];
    const lastPosition = positions[positions.length - 1];
    if (viewportCenter < firstPosition) {
        return direction === "next" ? 0 : null;
    }
    if (viewportCenter > lastPosition) {
        return direction === "prev" ? positions.length - 1 : null;
    }

    const nearestIndex = window.fileDiffNav.findNearestIndex(
        positions,
        viewportCenter,
    );
    return stepHunkIndexWithoutWrap(nearestIndex, direction, positions.length);
}

function navigateHunk(direction, { wrap = true } = {}) {
    const rows = getVisibleHunkRows();
    if (!rows.length) {
        return false;
    }

    const positions = window.fileDiffNav.uniqueSortedPositions(
        rows.map((row) => row.getBoundingClientRect().top + window.scrollY),
    );
    if (!positions.length) {
        return false;
    }

    const viewportCenter = window.scrollY + window.innerHeight / 2;
    const targetIndex = shouldUseActiveHunkIndex(positions, viewportCenter)
        ? (
            wrap
                ? window.fileDiffNav.stepHunkIndex(
                    hunkNavState.activeIndex,
                    direction,
                    positions.length,
                )
                : stepHunkIndexWithoutWrap(
                    hunkNavState.activeIndex,
                    direction,
                    positions.length,
                )
        )
        : (
            wrap
                ? window.fileDiffNav.pickTargetIndex(
                    positions,
                    viewportCenter,
                    direction,
                )
                : pickTargetIndexWithoutWrap(
                    positions,
                    viewportCenter,
                    direction,
                )
        );
    if (targetIndex === null) {
        return false;
    }

    const targetPosition = positions[targetIndex];
    hunkNavState.activeIndex = targetIndex;
    hunkNavState.signature = positionsSignature(positions);
    hunkNavState.lastNavAt = Date.now();
    syncSelectedHunk(targetIndex);

    const targetScrollTop = Math.max(
        Math.round(targetPosition - window.innerHeight / 2),
        0,
    );
    appendDebugScrollLog("hunkNav", `active=${targetIndex} target=${targetScrollTop}`);
    appendDebugScrollLog("scrollTo", `window.scrollTo top=${targetScrollTop} behavior=smooth from=${Math.round(window.scrollY)}`);
    window.scrollTo({ top: targetScrollTop, behavior: "smooth" });
    return true;
}

function stopHunkHold(button = hunkHoldState.button, { suppressClick = false } = {}) {
    if (hunkHoldState.rafId) {
        cancelAnimationFrame(hunkHoldState.rafId);
    }
    hunkHoldState.rafId = 0;

    if (button && suppressClick) {
        suppressedHunkClick.button = button;
        suppressedHunkClick.until = Date.now() + HUNK_HOLD_SUPPRESS_CLICK_MS;
    }

    clearHunkHoldVisual(hunkHoldState.button);
    hunkHoldState.button = null;
    hunkHoldState.direction = null;
    hunkHoldState.startAt = 0;
    hunkHoldState.emittedRepeats = 0;
}

function suppressNextHunkClick(button) {
    if (!button) return;
    markHunkClickSuppressed(button);
    suppressedHunkClick.button = button;
    suppressedHunkClick.until = Date.now() + HUNK_HOLD_SUPPRESS_CLICK_MS;
}

function tickHunkHold(now) {
    if (!hunkHoldState.button || !hunkHoldState.direction) {
        return;
    }

    const elapsed = now - hunkHoldState.startAt;
    const armingProgress = Math.max(
        0,
        Math.min(elapsed / HUNK_HOLD_DELAY_MS, 1),
    );
    const repeatElapsedMs = Math.max(0, elapsed - HUNK_HOLD_DELAY_MS);
    const repeatElapsedSeconds = repeatElapsedMs / 1000;
    const targetRepeats = Math.max(
        0,
        Math.floor(
            1.3 * repeatElapsedSeconds
                + 1.8 * repeatElapsedSeconds * repeatElapsedSeconds,
        ),
    );

    while (hunkHoldState.emittedRepeats < targetRepeats) {
        const moved = navigateHunk(hunkHoldState.direction, { wrap: false });
        if (!moved) {
            suppressNextHunkClick(hunkHoldState.button);
            stopHunkHold(hunkHoldState.button, {
                suppressClick: false,
            });
            return;
        }
        hunkHoldState.emittedRepeats += 1;
    }

    setHunkHoldVisual(
        hunkHoldState.button,
        armingProgress,
        hunkHoldState.emittedRepeats > 0,
    );
    hunkHoldState.rafId = requestAnimationFrame(tickHunkHold);
}

function startHunkHold(button, direction) {
    if (hunkHoldState.button === button && hunkHoldState.direction === direction) {
        return;
    }

    stopHunkHold();
    hunkHoldState.button = button;
    hunkHoldState.direction = direction;
    hunkHoldState.startAt = performance.now();
    setHunkHoldVisual(button, 0, false);
    hunkHoldState.rafId = requestAnimationFrame(tickHunkHold);
}

function bindHunkButton(button, direction) {
    const stopHold = () => {
        stopHunkHold(button, {
            suppressClick: hunkHoldState.emittedRepeats > 0,
        });
    };

    button.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        startHunkHold(button, direction);
    });
    button.addEventListener("mousedown", (event) => {
        if (event.button !== 0) return;
        startHunkHold(button, direction);
    });

    button.addEventListener("pointerup", stopHold);
    button.addEventListener("mouseup", stopHold);
    button.addEventListener("pointerleave", stopHold);
    button.addEventListener("mouseleave", stopHold);
    button.addEventListener("pointercancel", stopHold);

    button.addEventListener("click", (event) => {
        if (
            button.dataset.suppressHoldClick === "true"
            || (
                suppressedHunkClick.button === button
                && suppressedHunkClick.until > Date.now()
            )
        ) {
            clearSuppressedHunkClick(button);
            suppressedHunkClick.button = null;
            suppressedHunkClick.until = 0;
            event.preventDefault();
            return;
        }

        clearSuppressedHunkClick(button);
        suppressedHunkClick.button = null;
        suppressedHunkClick.until = 0;
        navigateHunk(direction);
    });
}

async function loadDiff() {
    return loadDiffWithOptions({});
}

async function loadDiffWithOptions(options = {}) {
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
    setForceParam(params, !!options.force);
    history.replaceState({}, "", `/?${params.toString()}`);
    setStatus("Loading diff…");
    resetHunkCaches();
    closeActiveDiffStream();
    const loadToken = ++activeLoadToken;

    try {
        if (shouldStreamDiff(state)) {
            const checkParams = new URLSearchParams(params);
            checkParams.set("check", "1");
            const checkResponse = await fetch(`/api/diff?${checkParams.toString()}`);
            const checkPayload = await checkResponse.json();
            if (loadToken !== activeLoadToken) {
                return;
            }
            if (!checkResponse.ok) {
                renderLoadError(state, checkPayload.error || "Failed to load diff.", params, checkPayload);
                return;
            }
            streamDiff(params, state, loadToken);
            return;
        }
        const response = await fetch(`/api/diff?${params.toString()}`);
        const payload = await response.json();
        if (loadToken !== activeLoadToken) {
            return;
        }
        if (!response.ok) {
            renderLoadError(state, payload.error || "Failed to load diff.", params, payload);
            return;
        }

        renderSummary(payload.summary, payload.mode);
        renderResult(payload);
        syncSelectedHunk(null);
        setStatus(buildStatusMessage(state, payload));
    } catch (error) {
        if (loadToken !== activeLoadToken) {
            return;
        }
        renderLoadError(state, error.message, params);
    }
}

function renderLoadError(state, message, params, payload = null) {
    summaryGrid.replaceChildren();
    resultPanel.replaceChildren();
    resetHunkCaches();
    syncSelectedHunk(null);

    const box = document.createElement("div");
    box.className = "error-state";
    box.textContent = message;
    if (payload?.can_force) {
        box.append(" ");
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Show anyway";
        button.addEventListener("click", () => {
            loadDiffWithOptions({ force: true });
        });
        box.append(button);
    }
    resultPanel.append(box);
    setStatus(payload?.can_force ? "Large diff blocked" : message, true);
    closeActiveDiffStream();
}

function streamDiff(params, state, loadToken) {
    const stream = new EventSource(`/api/diff-stream?${params.toString()}`);
    activeDiffStream = stream;
    let streamState = null;
    let finished = false;

    const fail = (message) => {
        if (loadToken !== activeLoadToken) {
            return;
        }
        finished = true;
        closeActiveDiffStream();
        summaryGrid.replaceChildren();
        resultPanel.replaceChildren();
        resetHunkCaches();
        syncSelectedHunk(null);

        const box = document.createElement("div");
        box.className = "error-state";
        box.textContent = message;
        resultPanel.append(box);
        setStatus(message, true);
    };

    stream.addEventListener("init", (event) => {
        if (loadToken !== activeLoadToken) {
            closeActiveDiffStream();
            return;
        }
        const payload = JSON.parse(event.data);
        streamState = beginRepoStream(payload);
        setStatus(`${buildStatusMessage(state, payload)} · streaming…`);
    });

    stream.addEventListener("file", (event) => {
        if (loadToken !== activeLoadToken || !streamState) {
            return;
        }
        const payload = JSON.parse(event.data);
        appendRepoStreamEntry(streamState, payload.entry, payload.summary);
        const loadedFiles = streamState.payload.files.length;
        const skippedFiles = payload.summary.skipped_files || 0;
        setStatus(
            `${buildStatusMessage(state, streamState.payload)} · loaded ${loadedFiles} file${loadedFiles !== 1 ? "s" : ""}${skippedFiles ? `, skipped ${skippedFiles}` : ""}`,
        );
    });

    stream.addEventListener("done", () => {
        if (loadToken !== activeLoadToken || !streamState) {
            return;
        }
        finished = true;
        closeActiveDiffStream();
        if (!streamState.payload.files.length) {
            const box = document.createElement("div");
            box.className = "error-state";
            box.textContent = "No changed files for the selected sides.";
            resultPanel.replaceChildren(box);
        }
        setStatus(buildStatusMessage(state, streamState.payload));
    });

    stream.addEventListener("error", (event) => {
        if (finished) {
            return;
        }
        try {
            const payload = event.data ? JSON.parse(event.data) : null;
            fail(payload?.error || "Failed to stream diff.");
        } catch {
            fail("Failed to stream diff.");
        }
    });
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

window.addEventListener("blur", () => {
    stopHunkHold();
});

bindHunkButton(prevHunkBtn, "prev");
bindHunkButton(nextHunkBtn, "next");

window.addEventListener("keydown", (event) => {
    if (shouldIgnoreHunkNavKeyEvent(event)) {
        return;
    }
    if (event.key === "n" && !event.shiftKey) {
        navigateHunk("next");
    } else if (event.key === "N") {
        navigateHunk("prev");
    }
});
window.addEventListener(
    "scroll",
    () => {
        lastScrollAt = performance.now();
        appendDebugScrollLog("scroll", `window.scrollY=${Math.round(window.scrollY)}`);
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
mountDebugScrollPanel();
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

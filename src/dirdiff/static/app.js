const form = document.getElementById("controlsForm");
const modeInput = document.getElementById("modeInput");
const modeButtonRow = document.getElementById("modeButtonRow");
const modeHint = document.getElementById("modeHint");
const branchReviewGroup = document.getElementById("branchReviewGroup");
const baseRemoteInput = document.getElementById("baseRemoteInput");
const baseBranchInput = document.getElementById("baseBranchInput");
const branchRemoteInput = document.getElementById("branchRemoteInput");
const branchInput = document.getElementById("branchInput");
const customRefsGroup = document.getElementById("customRefsGroup");
const leftRefInput = document.getElementById("leftRefInput");
const rightRefInput = document.getElementById("rightRefInput");
const statusText = document.getElementById("statusText");
const summaryGrid = document.getElementById("summaryGrid");
const resultPanel = document.getElementById("resultPanel");
const topHunkBtn = document.getElementById("topHunkBtn");
const prevHunkBtn = document.getElementById("prevHunkBtn");
const nextHunkBtn = document.getElementById("nextHunkBtn");
const debugMenu = document.getElementById("debugMenu");
const debugMenuToggle = document.getElementById("debugMenuToggle");
const debugMenuPanel = document.getElementById("debugMenuPanel");
const debugScrollToggle = document.getElementById("debugScrollToggle");
const hunkScrollModeSelect = document.getElementById("hunkScrollModeSelect");
const debugFpsValue = document.getElementById("debugFpsValue");
const debugNodeCountValue = document.getElementById("debugNodeCountValue");
const debugSpanCountValue = document.getElementById("debugSpanCountValue");

const foldApi = window.fileDiffFolds || {};
const debugQuery = new URLSearchParams(window.location.search);
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
    remote_names: "Remotes",
    remote_branches: "Remote branches",
};
const hunkNavState = {
    activeHunkIndex: null,
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
const SUPPRESSED_SYNTAX_CLASS_PREFIXES = [
    "ts-punctuation",
    "ts-operator",
    "ts-variable",
    "ts-parameter",
    "ts-field",
    "ts-local",
];
const DEBUG_SETTINGS_KEY = "dirdiff.debug.settings";
let pendingLoadTimer = 0;
let activeLoadToken = 0;
let activeDiffStream = null;
let activeRenderPass = 0;
let currentPayload = null;
let debugScrollLog = [];
let debugScrollPanel = null;
let debugScrollBody = null;
let deferredRowDecorationObserver = null;
const deferredRowDecorations = new WeakMap();
const pendingRowDecorationTargets = new Set();
let rowDecorationFlushScheduled = false;
let fpsSampleLastAt = performance.now();
let fpsSampleFrames = 0;
let fpsDisplayLastAt = fpsSampleLastAt;
let fpsCurrentValue = 0;

function loadStoredDebugSettings() {
    try {
        const payload = window.localStorage.getItem(DEBUG_SETTINGS_KEY);
        if (!payload) {
            return {};
        }
        const parsed = JSON.parse(payload);
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
        return {};
    }
}

const initialDebugSettings = loadStoredDebugSettings();
const debugState = {
    scrollDebug: debugQuery.get("debug_scroll") === "1" || !!initialDebugSettings.scrollDebug,
    hunkScrollMode: (
        initialDebugSettings.hunkScrollMode === "auto"
        || initialDebugSettings.hunkScrollMode === "smooth"
        || initialDebugSettings.hunkScrollMode === "browser"
    )
        ? initialDebugSettings.hunkScrollMode
        : "browser",
};

function usesChromiumScrollBehavior() {
    const brands = navigator.userAgentData?.brands || [];
    if (brands.some(({ brand }) => /\b(?:Chromium|Google Chrome|Microsoft Edge|Opera)\b/i.test(brand))) {
        return true;
    }

    const userAgent = navigator.userAgent || "";
    return /\b(?:Chrome|Chromium|Edg|OPR)\//.test(userAgent) && !/\bFirefox\//.test(userAgent);
}

function persistDebugSettings() {
    window.localStorage.setItem(DEBUG_SETTINGS_KEY, JSON.stringify({
        scrollDebug: debugState.scrollDebug,
        hunkScrollMode: debugState.hunkScrollMode,
    }));
}

function getHunkScrollBehavior() {
    if (debugState.hunkScrollMode === "auto" || debugState.hunkScrollMode === "smooth") {
        return debugState.hunkScrollMode;
    }
    return usesChromiumScrollBehavior() ? "auto" : "smooth";
}

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

async function saveDebugLog(text) {
    const response = await fetch("/api/save-log", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({ text }),
    });
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.error || "Failed to save debug log.");
    }
    return payload.path;
}

function formatDebugScrollLogEntry(entry) {
    return `${entry.t}ms [${entry.tag}] ${entry.message}`;
}

function formatMetricCount(value) {
    return Number(value || 0).toLocaleString();
}

function updateDebugMetrics() {
    if (debugFpsValue) {
        debugFpsValue.textContent = fpsCurrentValue ? String(Math.round(fpsCurrentValue)) : "--";
    }
    if (debugNodeCountValue) {
        debugNodeCountValue.textContent = formatMetricCount(document.querySelectorAll("*").length);
    }
    if (debugSpanCountValue) {
        debugSpanCountValue.textContent = formatMetricCount(document.querySelectorAll("span").length);
    }
}

function tickDebugFps(now) {
    fpsSampleFrames += 1;
    const elapsed = now - fpsSampleLastAt;
    if (elapsed >= 400) {
        fpsCurrentValue = (fpsSampleFrames * 1000) / elapsed;
        fpsSampleLastAt = now;
        fpsSampleFrames = 0;
    }
    if (now - fpsDisplayLastAt >= 900) {
        updateDebugMetrics();
        fpsDisplayLastAt = now;
    }
    requestAnimationFrame(tickDebugFps);
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
    if (!debugState.scrollDebug) {
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
    if (!debugState.scrollDebug || debugScrollPanel) {
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

    const saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.className = "scroll-debug-download";
    saveButton.textContent = "Save file";
    saveButton.addEventListener("click", async () => {
        const text = debugScrollLog.map(formatDebugScrollLogEntry).join("\n");
        try {
            const path = await saveDebugLog(text);
            appendDebugScrollLog("debug", `Saved debug log to ${path}`);
        } catch (error) {
            appendDebugScrollLog("debug", error.message || "Failed to save debug log");
        }
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

    actions.append(copyButton, saveButton, clearButton);
    header.append(title, actions);

    debugScrollBody = document.createElement("pre");
    debugScrollBody.className = "scroll-debug-log";

    debugScrollPanel.append(header, debugScrollBody);
    document.body.append(debugScrollPanel);
    appendDebugScrollLog("debug", `Mounted panel at ${window.location.search || "?"}`);
}

function unmountDebugScrollPanel() {
    if (!debugScrollPanel) {
        return;
    }
    debugScrollPanel.remove();
    debugScrollPanel = null;
    debugScrollBody = null;
}

function syncDebugScrollPanelVisibility() {
    if (debugState.scrollDebug) {
        mountDebugScrollPanel();
        return;
    }
    unmountDebugScrollPanel();
}

function setDebugScrollEnabled(enabled) {
    debugState.scrollDebug = !!enabled;
    persistDebugSettings();
    syncDebugScrollPanelVisibility();
    if (debugState.scrollDebug) {
        appendDebugScrollLog("debug", "Enabled scroll debug");
    }
}

function setHunkScrollMode(mode) {
    if (!["browser", "smooth", "auto"].includes(mode)) {
        return;
    }
    debugState.hunkScrollMode = mode;
    persistDebugSettings();
    if (debugState.scrollDebug) {
        appendDebugScrollLog("debug", `Hunk scroll mode set to ${mode}`);
    }
}

function syncDebugMenuControls() {
    if (debugScrollToggle) {
        debugScrollToggle.checked = debugState.scrollDebug;
    }
    if (hunkScrollModeSelect) {
        hunkScrollModeSelect.value = debugState.hunkScrollMode;
    }
}

function positionDebugMenuPanel() {
    if (!debugMenuToggle || !debugMenuPanel || debugMenuPanel.hidden) {
        return;
    }

    const margin = 16;
    const rect = debugMenuToggle.getBoundingClientRect();
    const panelWidth = debugMenuPanel.offsetWidth || 240;
    const panelHeight = debugMenuPanel.offsetHeight || 220;
    const left = Math.min(
        Math.max(rect.left, margin),
        Math.max(margin, window.innerWidth - panelWidth - margin),
    );
    const top = Math.min(
        Math.max(rect.bottom + 10, margin),
        Math.max(margin, window.innerHeight - panelHeight - margin),
    );
    debugMenuPanel.style.setProperty("--debug-menu-left", `${Math.round(left)}px`);
    debugMenuPanel.style.setProperty("--debug-menu-top", `${Math.round(top)}px`);
}

function setDebugMenuOpen(open) {
    if (!debugMenuToggle || !debugMenuPanel) {
        return;
    }
    debugMenuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    debugMenuPanel.hidden = !open;
    if (open) {
        requestAnimationFrame(positionDebugMenuPanel);
    }
}

function setupDebugMenu() {
    if (!debugMenu || !debugMenuToggle || !debugMenuPanel) {
        return;
    }

    syncDebugMenuControls();
    syncDebugScrollPanelVisibility();

    debugMenuToggle.addEventListener("click", (event) => {
        event.stopPropagation();
        const nextOpen = debugMenuPanel.hidden;
        setDebugMenuOpen(nextOpen);
    });

    debugScrollToggle?.addEventListener("change", () => {
        setDebugScrollEnabled(debugScrollToggle.checked);
    });

    hunkScrollModeSelect?.addEventListener("change", () => {
        setHunkScrollMode(hunkScrollModeSelect.value);
    });

    debugMenuPanel.addEventListener("click", (event) => {
        event.stopPropagation();
    });

    document.addEventListener("click", (event) => {
        if (!debugMenu.contains(event.target)) {
            setDebugMenuOpen(false);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setDebugMenuOpen(false);
        }
    });

    window.addEventListener("resize", () => {
        if (!debugMenuPanel.hidden) {
            positionDebugMenuPanel();
        }
    });

    updateDebugMetrics();
    window.setInterval(updateDebugMetrics, 1000);
    requestAnimationFrame(tickDebugFps);
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

    const clusters = [
        summaryCluster(
            "Lines",
            [
                summaryDelta("+", summary.added_lines, "added", "Lines added"),
                summaryDelta("~", summary.modified_lines, "updated", "Lines updated"),
                summaryDelta("-", summary.removed_lines, "removed", "Lines removed"),
            ],
            "Line totals for this diff",
        ),
    ];
    if (Number.isInteger(summary.changed_cells)) {
        clusters.push(
            summaryCluster(
                "Cells",
                [
                    summaryDelta("+", summary.added_cells || 0, "added", "Cells added"),
                    summaryDelta("~", summary.modified_cells || 0, "updated", "Cells modified"),
                    summaryDelta("-", summary.removed_cells || 0, "removed", "Cells removed"),
                ],
                "Notebook cell totals",
            ),
        );
    }
    summaryGrid.replaceChildren(...clusters);
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

function listRemoteBranchChoices(refChoices, remoteName) {
    const normalizedRemote = remoteName.trim();
    if (!normalizedRemote) {
        return [];
    }
    const prefix = `${normalizedRemote}/`;
    return [...new Set(
        (refChoices.remotes || [])
            .filter((value) => value.startsWith(prefix))
            .map((value) => value.slice(prefix.length))
            .filter(Boolean),
    )].sort();
}

function filterValues(values, query) {
    const needle = query.trim().toLowerCase();
    return values.filter((value) => {
        if (!needle) {
            return true;
        }
        return value.toLowerCase().includes(needle);
    });
}

function attachAutocomplete(input, refChoicesOrResolver, sections) {
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
        const groups = typeof refChoicesOrResolver === "function"
            ? refChoicesOrResolver(input.value)
            : filterRefChoices(refChoicesOrResolver, input.value, sections);
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
                    input.dispatchEvent(new Event("change", { bubbles: true }));
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

function shouldRenderSyntaxWrapper(classes) {
    if (!classes || !classes.length) {
        return false;
    }
    return !classes.every((className) =>
        SUPPRESSED_SYNTAX_CLASS_PREFIXES.some(
            (prefix) => className === prefix || className.startsWith(`${prefix}-`),
        ),
    );
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
            const slice = text.slice(start, end);
            const classes = span.classes || [];
            if (shouldRenderSyntaxWrapper(classes)) {
                const node = document.createElement("span");
                node.className = ["ts-token", ...classes].join(" ");
                node.textContent = slice;
                contentEl.append(node);
            } else {
                contentEl.append(document.createTextNode(slice));
            }
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

function makeDiffSide(
    row,
    side,
    sideLabel,
    { deferDecoration = false } = {},
) {
    const sideEl = document.createElement("div");
    sideEl.className = `diff-side side-${side}`;
    sideEl.dataset.sideLabel = sideLabel;

    if (
        (row.status === "insert" && side === "left")
        || (row.status === "delete" && side === "right")
    ) {
        sideEl.classList.add("empty-side");
    }

    const noEl = document.createElement("div");
    noEl.className = "line-no";
    noEl.textContent = (side === "left" ? row.left_no : row.right_no) ?? "";

    const codeEl = document.createElement("code");
    codeEl.className = "line-code";

    const tokens = side === "left" ? row.left_tokens : row.right_tokens;
    const text = side === "left" ? row.left_text : row.right_text;
    const syntaxSpans = side === "left" ? row.left_syntax : row.right_syntax;

    if (deferDecoration) {
        queueDeferredRowDecoration(codeEl, text || " ", syntaxSpans, tokens);
    } else {
        applyRowDecoration(codeEl, text || " ", syntaxSpans, tokens);
    }

    sideEl.append(noEl, codeEl);
    return sideEl;
}

function makeDiffRow(
    row,
    leftLabel,
    rightLabel,
    markHunkAnchor = false,
    hunkIndex = null,
    { deferDecoration = false } = {},
) {
    const rowEl = document.createElement("div");
    rowEl.className = `diff-row ${row.status}`;

    if (
        markHunkAnchor
        && (row.status === "insert" || row.status === "delete" || row.status === "replace")
    ) {
        rowEl.classList.add("hunk-anchor");
        hunkAnchorRows.push(rowEl);
    }
    if (Number.isInteger(hunkIndex)) {
        rowEl.dataset.hunkIndex = String(hunkIndex);
        rowEl.classList.add("hunk-anchor-row");
        hunkRowsByIndex.set(hunkIndex, [rowEl]);
    }

    const changedTokens = [
        ...(row.left_tokens || []),
        ...(row.right_tokens || []),
    ];
    const hasNonWhitespaceTokenChanges = changedTokens.some(
        (tok) => tok.changed && !tok.is_ws,
    );
    const hasWhitespaceOnlyChanges = changedTokens.length
        && changedTokens.some((tok) => tok.changed)
        && !hasNonWhitespaceTokenChanges;

    if (hasWhitespaceOnlyChanges) {
        rowEl.classList.add("whitespace-only-change");
        rowEl.title = "Leading whitespace changed";
    }

    const leftSide = makeDiffSide(
        row,
        "left",
        leftLabel,
        { deferDecoration },
    );
    const rightSide = makeDiffSide(
        row,
        "right",
        rightLabel,
        { deferDecoration },
    );

    if (hasWhitespaceOnlyChanges) {
        leftSide.querySelector(".line-no")?.setAttribute("title", "Leading whitespace changed");
        rightSide.querySelector(".line-no")?.setAttribute("title", "Leading whitespace changed");
    }

    rowEl.append(leftSide, rightSide);
    return rowEl;
}

function isChangedRowStatus(status) {
    return status === "insert" || status === "delete" || status === "replace";
}

function makeFoldBarSide(count, label = "", sideLabel) {
    const bar = document.createElement("div");
    bar.className = "diff-side fold-side";
    bar.dataset.sideLabel = sideLabel;
    let text = `... ${count} line${count !== 1 ? "s" : ""}`;
    if (label) {
        text = `... ${count} line${count !== 1 ? "s" : ""} in ${label}`;
    }
    bar.innerHTML = `<div class="line-no">..</div><div class="fold-label">${escapeHtml(text)}</div>`;
    return bar;
}

function makeFoldBar(count, leftLabel, rightLabel, label = "") {
    const bar = document.createElement("div");
    bar.className = "diff-row fold-bar";
    bar.append(
        makeFoldBarSide(count, label, leftLabel),
        makeFoldBarSide(count, label, rightLabel),
    );
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
    rowsHost,
    leftLabel,
    rightLabel,
    startHunkIndex,
    renderPassId,
) {
    let nextHunkIndex = startHunkIndex;
    let renderIndex = 0;
    let cursor = 0;
    let lastRow = rowsHost.lastElementChild;

    while (cursor < processedRows.length) {
        if (renderPassId !== activeRenderPass) {
            return;
        }

        const rowsFragment = document.createDocumentFragment();
        const batchEnd = Math.min(cursor + ROW_RENDER_BATCH_SIZE, processedRows.length);

        for (; cursor < batchEnd; cursor += 1) {
            const row = processedRows[cursor];

            if (row.status === "elided") {
                const foldBar = makeFoldBar(row.count, leftLabel, rightLabel, row.label);
                rowsFragment.append(foldBar);
                lastRow = foldBar;
                continue;
            }

            if (row.status === "fold") {
                const foldBar = makeFoldBar(row.count, leftLabel, rightLabel, row.label);
                const expandedRows = [];
                const signatureRow = lastRow;
                const barAnchor = document.createComment("fold-bar-anchor");
                let expanded = false;

                rowsFragment.append(foldBar);
                rowsFragment.append(barAnchor);
                lastRow = foldBar;

                if (!signatureRow) {
                    continue;
                }

                const leftNo = signatureRow.querySelector(".diff-side.side-left .line-no");
                const rightNo = signatureRow.querySelector(".diff-side.side-right .line-no");
                if (!leftNo || !rightNo) {
                    continue;
                }

                const leftToggleIcon = makeInlineFoldToggle(toggleFold);
                const rightToggleIcon = makeInlineFoldToggle(toggleFold);

                leftNo.prepend(leftToggleIcon);
                rightNo.prepend(rightToggleIcon);

                signatureRow.classList.add("fold-toggle-row");
                signatureRow.title = "Toggle fold";

                setInlineFoldState(signatureRow, false);

                function toggleFold() {
                    expanded = !expanded;
                    if (expanded) {
                        row.foldedRows.forEach((foldedRow) => {
                            const rowNode = makeDiffRow(
                                foldedRow,
                                leftLabel,
                                rightLabel,
                                false,
                                null,
                                { deferDecoration: false },
                            );
                            expandedRows.push(rowNode);
                            rowsHost.insertBefore(rowNode, barAnchor);
                        });
                        foldBar.remove();
                        setInlineFoldState(signatureRow, true);
                        return;
                    }

                    expandedRows.splice(0).forEach((node) => node.remove());
                    rowsHost.insertBefore(foldBar, barAnchor);
                    setInlineFoldState(signatureRow, false);
                }

                foldBar.addEventListener("click", toggleFold);
                signatureRow.addEventListener("click", toggleFold);
                continue;
            }

            const previous = cursor > 0 ? processedRows[cursor - 1] : null;
            const markHunkAnchor =
                isChangedRowStatus(row.status)
                && !isChangedRowStatus(previous?.status ?? "equal");
            const anchorIndex = markHunkAnchor ? nextHunkIndex++ : null;
            const deferDecoration =
                row.status === "equal" && renderIndex >= EAGER_ROW_DECORATION_LIMIT;
            const rowNode = makeDiffRow(
                row,
                leftLabel,
                rightLabel,
                markHunkAnchor,
                anchorIndex,
                { deferDecoration },
            );
            rowsFragment.append(rowNode);
            lastRow = rowNode;
            renderIndex += 1;
        }

        rowsHost.append(rowsFragment);

        if (cursor < processedRows.length) {
            await yieldToBrowser();
        }
    }
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

    const header = document.createElement("div");
    header.className = "diff-header-row";
    header.innerHTML = `
        <div class="diff-pane-header diff-side-header">${escapeHtml(leftLabel)}</div>
        <div class="diff-pane-header diff-side-header">${escapeHtml(rightLabel)}</div>
    `;
    const rowsHost = document.createElement("div");
    rowsHost.className = "diff-lines";
    const nextHunkIndex = startHunkIndex + countHunkAnchors(processedRows);

    wrapper.append(header, rowsHost);
    const renderPromise = appendRenderedRowsInBatches(
        processedRows,
        rowsHost,
        leftLabel,
        rightLabel,
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

function notebookSectionSummary(label, payload) {
    const parts = [label];
    if (payload.render_mode === "plain") {
        parts.push("plain render");
    }
    if (payload.truncated_rows) {
        parts.push(`truncated ${payload.truncated_rows}`);
    }
    return parts.join(" · ");
}

function makeNotebookDetails(summaryText, renderContent) {
    const details = document.createElement("details");
    details.className = "notebook-details";
    const summary = document.createElement("summary");
    summary.textContent = summaryText;
    const host = document.createElement("div");
    let rendered = false;

    const ensureRendered = async () => {
        if (rendered) {
            return;
        }
        rendered = true;
        const loading = document.createElement("div");
        loading.className = "notebook-details-message";
        loading.textContent = "Loading…";
        host.replaceChildren(loading);
        try {
            const content = await renderContent();
            host.replaceChildren(content);
        } catch (error) {
            const message = document.createElement("div");
            message.className = "error-state";
            message.textContent = error?.message || "Failed to load notebook section.";
            host.replaceChildren(message);
        }
    };

    details.addEventListener("toggle", () => {
        if (details.open) {
            void ensureRendered();
        }
    });
    details.append(summary, host);
    return { details, ensureRendered };
}

function getNextHunkIndexForRows(rows, startHunkIndex, foldHints = []) {
    const processedRows = foldApi.addFoldRows ? foldApi.addFoldRows(rows, foldHints) : rows;
    return startHunkIndex + countHunkAnchors(processedRows);
}

function getNextHunkIndexForSection(hunkCount, startHunkIndex) {
    return startHunkIndex + Math.max(Number(hunkCount) || 0, 0);
}

async function fetchNotebookSection(filePayload, { section, cellKey = null }) {
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
    if (state.reviewBranch) {
        params.set("review_branch", state.reviewBranch);
    }
    if (filePayload.left_path) {
        params.set("left_path", filePayload.left_path);
    }
    if (filePayload.right_path) {
        params.set("right_path", filePayload.right_path);
    }
    params.set("section", section);
    if (cellKey) {
        params.set("cell_key", cellKey);
    }

    const response = await fetch(`/api/notebook-section?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.error || "Failed to load notebook section.");
    }
    return payload;
}

function makeNotebookSection(
    rows,
    leftLabel,
    rightLabel,
    startHunkIndex,
    renderPassId,
    {
        heading = null,
        foldHints = [],
        renderMode = null,
        truncatedRows = 0,
    } = {},
) {
    const host = document.createElement("section");
    host.className = "notebook-section";
    if (heading) {
        const headingNode = document.createElement("p");
        headingNode.className = "notebook-section-heading";
        headingNode.textContent = heading;
        host.append(headingNode);
    }

    const { wrapper, nextHunkIndex, renderPromise } = renderSideBySide(
        rows,
        leftLabel,
        rightLabel,
        startHunkIndex,
        foldHints,
        renderPassId,
    );
    host.append(wrapper);

    const payload = {};
    if (renderMode) {
        payload.render_mode = renderMode;
    }
    if (truncatedRows) {
        payload.truncated_rows = truncatedRows;
    }

    return {
        host,
        payload,
        nextHunkIndex,
        renderPromise,
    };
}

function makeNotebookCellCard(
    filePayload,
    cell,
    startHunkIndex = 0,
    renderPassId = activeRenderPass,
) {
    const card = document.createElement("article");
    card.className = "file-card notebook-cell-card";

    const title = document.createElement("h3");
    title.className = "file-title";
    title.textContent = `${cell.kind.toUpperCase()} ${cell.cell_type} cell`;

    const subtitle = document.createElement("p");
    subtitle.className = "file-subtitle";
    subtitle.textContent = `Cell ID: ${cell.cell_id ?? "missing"} · left #${cell.left_index ?? "—"} · right #${cell.right_index ?? "—"}`;

    const titleWrap = document.createElement("div");
    titleWrap.append(title, subtitle);

    const badges = document.createElement("div");
    badges.className = "badge-row";
    const kindBadgeClass =
        cell.kind === "added"
            ? "badge-added"
            : cell.kind === "removed"
            ? "badge-removed"
            : "badge-modified";
    badges.append(badge(cell.kind, kindBadgeClass));
    if (cell.metadata_changed) {
        badges.append(badge("metadata changed", "badge-neutral"));
    }
    if (cell.outputs_changed) {
        badges.append(badge("outputs changed", "badge-neutral"));
    }
    if (!cell.source_changed) {
        badges.append(badge("source unchanged", "badge-neutral"));
    }

    const header = document.createElement("div");
    header.className = "file-card-header";
    header.append(titleWrap, badges);
    card.append(header);

    const renderPromises = [];
    const sourceSection = makeNotebookSection(
        cell.source_rows,
        "Left source",
        "Right source",
        startHunkIndex,
        renderPassId,
        {
            heading: "Cell source",
            foldHints: cell.source_fold_hints || [],
            renderMode: cell.source_render_mode || null,
            truncatedRows: cell.source_truncated_rows || 0,
        },
    );
    card.append(sourceSection.host);
    renderPromises.push(sourceSection.renderPromise);
    let nextHunkIndex = sourceSection.nextHunkIndex;

    if (cell.metadata_changed) {
        const metadataStartHunkIndex = nextHunkIndex;
        nextHunkIndex = getNextHunkIndexForSection(
            cell.metadata_hunk_count,
            metadataStartHunkIndex,
        );
        const metadataDetails = makeNotebookDetails(
            notebookSectionSummary("Cell metadata diff", {
                render_mode: cell.metadata_render_mode || null,
                truncated_rows: cell.metadata_truncated_rows || 0,
            }),
            async () => {
                if (!cell.metadata_section) {
                    cell.metadata_section = await fetchNotebookSection(filePayload, {
                        section: "cell-metadata",
                        cellKey: cell.cell_key,
                    });
                }
                const metadataSection = makeNotebookSection(
                    cell.metadata_section.rows,
                    "Left metadata",
                    "Right metadata",
                    metadataStartHunkIndex,
                    renderPassId,
                    {
                        renderMode: cell.metadata_section.render_mode || null,
                        truncatedRows: cell.metadata_section.truncated_rows || 0,
                    },
                );
                return metadataSection.host;
            },
        );
        card.append(metadataDetails.details);
    }

    if (cell.outputs_changed) {
        const outputsStartHunkIndex = nextHunkIndex;
        nextHunkIndex = getNextHunkIndexForSection(
            cell.outputs_hunk_count,
            outputsStartHunkIndex,
        );
        const outputsDetails = makeNotebookDetails(
            notebookSectionSummary("Cell outputs diff", {
                render_mode: cell.outputs_render_mode || null,
                truncated_rows: cell.outputs_truncated_rows || 0,
            }),
            async () => {
                if (!cell.outputs_section) {
                    cell.outputs_section = await fetchNotebookSection(filePayload, {
                        section: "cell-outputs",
                        cellKey: cell.cell_key,
                    });
                }
                const outputsSection = makeNotebookSection(
                    cell.outputs_section.rows,
                    "Left outputs",
                    "Right outputs",
                    outputsStartHunkIndex,
                    renderPassId,
                    {
                        renderMode: cell.outputs_section.render_mode || null,
                        truncatedRows: cell.outputs_section.truncated_rows || 0,
                    },
                );
                return outputsSection.host;
            },
        );
        card.append(outputsDetails.details);
    }

    return {
        card,
        nextHunkIndex,
        renderPromise: Promise.all(renderPromises),
    };
}

function makeNotebookFileCard(payload, startHunkIndex = 0, renderPassId = activeRenderPass) {
    const card = document.createElement("article");
    card.className = "file-card notebook-file-card";
    const body = document.createElement("div");
    body.className = "file-card-body";

    const title = document.createElement("h2");
    title.className = "file-title";
    title.textContent = payload.display_name;

    const subtitle = document.createElement("p");
    subtitle.className = "file-subtitle";
    subtitle.textContent =
        payload.mode === "git" ? "Notebook-aware Git-backed file diff" : "Notebook-aware file diff";

    const titleWrap = document.createElement("div");
    titleWrap.className = "file-card-heading";
    titleWrap.append(title, subtitle);

    const badges = document.createElement("div");
    badges.className = "badge-row";
    badges.append(
        badge(payload.summary.left_exists ? "left exists" : "left missing", "badge-neutral"),
        badge(payload.summary.right_exists ? "right exists" : "right missing", "badge-neutral"),
        badge(`${payload.summary.changed_cells || 0} changed cell${payload.summary.changed_cells === 1 ? "" : "s"}`, "badge-neutral"),
    );
    if (payload.summary.notebook_metadata_changed) {
        badges.append(badge("notebook metadata changed", "badge-neutral"));
    }

    const headerActions = document.createElement("div");
    headerActions.className = "file-card-header-actions";
    headerActions.append(badges);

    const header = document.createElement("div");
    header.className = "file-card-header";
    header.append(titleWrap, headerActions);
    header.prepend(
        makeCollapsibleHeader(card, header, body, {
            expandedLabel: `Collapse file ${payload.display_name}`,
            collapsedLabel: `Expand file ${payload.display_name}`,
        }),
    );
    card.append(header);

    const renderPromises = [];
    let nextHunkIndex = startHunkIndex;

    if (payload.summary.notebook_metadata_changed) {
        const notebookMetadataStartHunkIndex = nextHunkIndex;
        nextHunkIndex = getNextHunkIndexForSection(
            payload.notebook_metadata_hunk_count,
            notebookMetadataStartHunkIndex,
        );
        const metadataDetails = makeNotebookDetails(
            notebookSectionSummary("Notebook metadata diff", {
                render_mode: payload.notebook_metadata_render_mode || null,
                truncated_rows: payload.notebook_metadata_truncated_rows || 0,
            }),
            async () => {
                if (!payload.notebook_metadata_section) {
                    payload.notebook_metadata_section = await fetchNotebookSection(
                        payload,
                        { section: "notebook-metadata" },
                    );
                }
                const metadataSection = makeNotebookSection(
                    payload.notebook_metadata_section.rows,
                    "Left notebook metadata",
                    "Right notebook metadata",
                    notebookMetadataStartHunkIndex,
                    renderPassId,
                    {
                        renderMode: payload.notebook_metadata_section.render_mode || null,
                        truncatedRows: payload.notebook_metadata_section.truncated_rows || 0,
                    },
                );
                return metadataSection.host;
            },
        );
        body.append(metadataDetails.details);
    }

    const cellsHost = document.createElement("div");
    cellsHost.className = "notebook-cells";
    for (const cell of payload.cells || []) {
        const cellResult = makeNotebookCellCard(
            payload,
            cell,
            nextHunkIndex,
            renderPassId,
        );
        nextHunkIndex = cellResult.nextHunkIndex;
        renderPromises.push(cellResult.renderPromise);
        cellsHost.append(cellResult.card);
    }
    if (!cellsHost.childElementCount) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "No changed cells detected for the selected notebook sides.";
        cellsHost.append(empty);
    }
    body.append(cellsHost);
    card.append(body);

    return {
        card,
        nextHunkIndex,
        renderPromise: Promise.all(renderPromises),
    };
}

function makeFileCard(payload, startHunkIndex = 0, renderPassId = activeRenderPass) {
    if (payload.render_kind === "notebook") {
        return makeNotebookFileCard(payload, startHunkIndex, renderPassId);
    }

    const card = document.createElement("article");
    card.className = "file-card";
    const body = document.createElement("div");
    body.className = "file-card-body";

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
    titleWrap.className = "file-card-heading";
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

    const headerActions = document.createElement("div");
    headerActions.className = "file-card-header-actions";
    headerActions.append(badges);

    const header = document.createElement("div");
    header.className = "file-card-header";
    header.append(titleWrap, headerActions);
    header.prepend(
        makeCollapsibleHeader(card, header, body, {
            expandedLabel: `Collapse file ${payload.display_name}`,
            collapsedLabel: `Expand file ${payload.display_name}`,
        }),
    );
    card.append(header);
    const { wrapper, nextHunkIndex, renderPromise } = renderSideBySide(
        payload.rows,
        payload.left_label,
        payload.right_label,
        startHunkIndex,
        payload.fold_hints || [],
        renderPassId,
    );
    body.append(wrapper);
    card.append(body);

    return {
        card,
        nextHunkIndex,
        renderPromise,
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

        const repoView = makeRepoGroupView();
        resultPanel.append(repoView.controls, repoView.groupsHost);
        let nextHunkIndex = 0;
        payload.files.forEach((entry) => {
            if (entry.error) {
                repoView.appendEntry(entry, makeErrorCard(entry));
                return;
            }
            const result = makeFileCard(entry, nextHunkIndex, renderPassId);
            nextHunkIndex = result.nextHunkIndex;
            repoView.appendEntry(entry, result.card);
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
    if (state.reviewBranch) {
        params.set("review_branch", state.reviewBranch);
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
    const repoView = makeRepoGroupView();
    resultPanel.append(repoView.controls, repoView.groupsHost);

    const payload = {
            ...initialPayload,
            files: [],
    };
    currentPayload = payload;
    return {
        payload,
        nextHunkIndex: 0,
        renderPassId,
        repoView,
    };
}

function appendRepoStreamEntry(streamState, entry, summary) {
    streamState.payload.summary = summary;
    streamState.payload.files.push(entry);
    currentPayload = streamState.payload;
    renderSummary(summary, streamState.payload.mode);

    if (entry.error) {
        streamState.repoView.appendEntry(entry, makeErrorCard(entry));
        return;
    }

    const result = makeFileCard(entry, streamState.nextHunkIndex, streamState.renderPassId);
    streamState.nextHunkIndex = result.nextHunkIndex;
    streamState.repoView.appendEntry(entry, result.card);
}

function isVisibleHunkAnchor(row) {
    return !!row && row.offsetParent !== null && row.getClientRects().length > 0;
}

let hunkAnchorRows = [];
const hunkRowsByIndex = new Map();
let selectedHunkIndex = null;
let nextFileCardBodyId = 0;

function resetHunkCaches() {
    hunkAnchorRows = [];
    hunkRowsByIndex.clear();
    selectedHunkIndex = null;
    hunkNavState.activeHunkIndex = null;
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

function getHunkIndex(row) {
    if (!row) {
        return null;
    }
    const value = Number(row.dataset.hunkIndex);
    return Number.isInteger(value) ? value : null;
}

function getViewportCenterY() {
    return window.innerHeight / 2;
}

function getRowViewportCenter(row) {
    const rect = row.getBoundingClientRect();
    return rect.top + rect.height / 2;
}

function findNearestHunkRow(rows, viewportCenter) {
    if (!rows.length) {
        return null;
    }

    let nearestRow = rows[0];
    let nearestDistance = Math.abs(getRowViewportCenter(rows[0]) - viewportCenter);
    for (let index = 1; index < rows.length; index += 1) {
        const distance = Math.abs(getRowViewportCenter(rows[index]) - viewportCenter);
        if (distance < nearestDistance) {
            nearestDistance = distance;
            nearestRow = rows[index];
        }
    }
    return nearestRow;
}

function findRowIndex(rows, targetRow) {
    if (!targetRow) {
        return -1;
    }
    return rows.findIndex((row) => row === targetRow);
}

function stepVisibleHunkRow(rows, currentRow, direction, { wrap = true } = {}) {
    const currentIndex = findRowIndex(rows, currentRow);
    if (currentIndex < 0 || !rows.length) {
        return null;
    }

    if (direction === "next") {
        if (currentIndex + 1 < rows.length) {
            return rows[currentIndex + 1];
        }
        return wrap ? rows[0] : null;
    }

    if (currentIndex - 1 >= 0) {
        return rows[currentIndex - 1];
    }
    return wrap ? rows[rows.length - 1] : null;
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

function clearActiveHunkSelection() {
    hunkNavState.activeHunkIndex = null;
    hunkNavState.lastNavAt = 0;
    syncSelectedHunk(null);
}

function makeCollapsibleHeader(
    container,
    header,
    body,
    {
        indicatorClassName = "file-collapse-indicator",
        expandedLabel = "Collapse section",
        collapsedLabel = "Expand section",
    } = {},
) {
    const indicator = document.createElement("span");
    indicator.className = indicatorClassName;
    indicator.setAttribute("aria-hidden", "true");

    const bodyId = `file-card-body-${++nextFileCardBodyId}`;
    body.id = bodyId;
    header.classList.add("collapsible-header");
    header.tabIndex = 0;
    header.setAttribute("role", "button");
    header.setAttribute("aria-controls", bodyId);

    function setExpanded(expanded) {
        body.hidden = !expanded;
        container.classList.toggle("is-collapsed", !expanded);
        header.setAttribute("aria-expanded", expanded ? "true" : "false");
        header.setAttribute("aria-label", expanded ? expandedLabel : collapsedLabel);
        indicator.textContent = expanded ? "▾" : "▸";
    }

    header.setExpanded = setExpanded;
    setExpanded(true);

    function toggleExpanded() {
        const nextExpanded = body.hidden;
        if (!nextExpanded) {
            clearActiveHunkSelection();
        }
        setExpanded(nextExpanded);
    }

    header.addEventListener("click", toggleExpanded);
    header.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
            return;
        }
        event.preventDefault();
        toggleExpanded();
    });
    return indicator;
}

function setExpandablesExpanded(expandables, expanded) {
    if (!expanded) {
        clearActiveHunkSelection();
    }
    expandables.forEach((expandable) => {
        if (typeof expandable.setExpanded === "function") {
            expandable.setExpanded(expanded);
        }
    });
}

function entryDirectoryPath(entry) {
    const pathCandidate = String(entry.right_path || entry.left_path || entry.display_name || "").trim();
    const normalizedPath = pathCandidate.includes(" -> ")
        ? pathCandidate.split(" -> ").at(-1).trim()
        : pathCandidate;
    const lastSlash = normalizedPath.lastIndexOf("/");
    return lastSlash >= 0 ? normalizedPath.slice(0, lastSlash) : "";
}

function entryDirectoryLabel(entry) {
    return entryDirectoryPath(entry) || "root files";
}

function makeDirectoryGroup(label) {
    const section = document.createElement("section");
    section.className = "directory-group";
    const body = document.createElement("div");
    body.className = "directory-group-body";

    const title = document.createElement("h2");
    title.className = "directory-group-title";
    title.textContent = label;

    const countBadge = badge("0 files", "badge-neutral");
    let itemCount = 0;

    const headerActions = document.createElement("div");
    headerActions.className = "directory-group-actions";
    headerActions.append(countBadge);

    const header = document.createElement("div");
    header.className = "directory-group-header";

    const heading = document.createElement("div");
    heading.className = "directory-group-heading";
    heading.append(
        makeCollapsibleHeader(section, header, body, {
            indicatorClassName: "directory-collapse-indicator",
            expandedLabel: `Collapse directory ${label}`,
            collapsedLabel: `Expand directory ${label}`,
        }),
        title,
    );

    header.append(heading, headerActions);

    section.append(header, body);
    return {
        section,
        body,
        append(node) {
            itemCount += 1;
            countBadge.textContent = `${itemCount} file${itemCount === 1 ? "" : "s"}`;
            body.append(node);
        },
    };
}

function makeRepoGroupView() {
    const controls = document.createElement("div");
    controls.className = "repo-fold-controls";

    const foldAllButton = document.createElement("button");
    foldAllButton.type = "button";
    foldAllButton.className = "file-collapse-toggle repo-collapse-toggle";
    foldAllButton.textContent = "Fold all";

    const showAllButton = document.createElement("button");
    showAllButton.type = "button";
    showAllButton.className = "file-collapse-toggle repo-collapse-toggle";
    showAllButton.textContent = "Show all";

    const groupsHost = document.createElement("div");
    groupsHost.className = "directory-groups";
    const directoryGroups = new Map();

    function allExpandables() {
        return [
            ...groupsHost.querySelectorAll(".directory-group-header"),
            ...groupsHost.querySelectorAll(".file-card-header"),
        ];
    }

    foldAllButton.addEventListener("click", () => {
        setExpandablesExpanded(allExpandables(), false);
    });
    showAllButton.addEventListener("click", () => {
        setExpandablesExpanded(allExpandables(), true);
    });

    controls.append(foldAllButton, showAllButton);

    function ensureGroup(entry) {
        const label = entryDirectoryLabel(entry);
        let group = directoryGroups.get(label);
        if (!group) {
            group = makeDirectoryGroup(label);
            directoryGroups.set(label, group);
            groupsHost.append(group.section);
        }
        return group;
    }

    return {
        controls,
        groupsHost,
        appendEntry(entry, node) {
            ensureGroup(entry).append(node);
        },
    };
}

function getActiveHunkRowForNavigation(viewportCenter) {
    if (!Number.isInteger(hunkNavState.activeHunkIndex)) {
        return null;
    }
    const activeRows = hunkRowsByIndex.get(hunkNavState.activeHunkIndex) || [];
    const activeRow = activeRows.find((row) => isVisibleHunkAnchor(row));
    if (!activeRow) {
        return null;
    }
    if (Date.now() - hunkNavState.lastNavAt < 900) {
        return activeRow;
    }
    return Math.abs(getRowViewportCenter(activeRow) - viewportCenter) <= 24
        ? activeRow
        : null;
}

function pickTargetHunkRow(rows, viewportCenter, direction, { wrap = true } = {}) {
    if (!rows.length) {
        return null;
    }

    const firstCenter = getRowViewportCenter(rows[0]);
    const lastCenter = getRowViewportCenter(rows[rows.length - 1]);
    if (viewportCenter < firstCenter) {
        if (direction === "next") {
            return rows[0];
        }
        return wrap ? rows[rows.length - 1] : null;
    }
    if (viewportCenter > lastCenter) {
        if (direction === "prev") {
            return rows[rows.length - 1];
        }
        return wrap ? rows[0] : null;
    }

    const nearestRow = findNearestHunkRow(
        rows,
        viewportCenter,
    );
    return stepVisibleHunkRow(rows, nearestRow, direction, { wrap });
}

function navigateHunk(direction, { wrap = true } = {}) {
    const rows = getVisibleHunkRows();
    if (!rows.length) {
        return false;
    }

    const viewportCenter = getViewportCenterY();
    const activeRow = getActiveHunkRowForNavigation(viewportCenter);
    const targetRow = activeRow
        ? stepVisibleHunkRow(rows, activeRow, direction, { wrap })
        : pickTargetHunkRow(rows, viewportCenter, direction, { wrap });
    if (!targetRow) {
        return false;
    }

    const targetHunkIndex = getHunkIndex(targetRow);
    if (!Number.isInteger(targetHunkIndex)) {
        return false;
    }

    hunkNavState.activeHunkIndex = targetHunkIndex;
    hunkNavState.lastNavAt = Date.now();
    syncSelectedHunk(targetHunkIndex);

    const rowCenter = Math.round(getRowViewportCenter(targetRow));
    appendDebugScrollLog(
        "hunkNav",
        `hunk=${targetHunkIndex} direction=${direction} rowCenter=${rowCenter}`,
    );
    appendDebugScrollLog(
        "scrollTo",
        `row.scrollIntoView hunk=${targetHunkIndex} block=center behavior=${getHunkScrollBehavior()}`,
    );
    targetRow.scrollIntoView({ block: "center", behavior: getHunkScrollBehavior() });
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

async function loadDiffWithOptions() {
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
    if (state.reviewBranch) {
        params.set("review_branch", state.reviewBranch);
    }
    history.replaceState({}, "", `/?${params.toString()}`);
    setStatus("Loading diff…");
    resetHunkCaches();
    closeActiveDiffStream();
    const loadToken = ++activeLoadToken;

    try {
        if (shouldStreamDiff(state)) {
            streamDiff(params, state, loadToken);
            return;
        }
        const response = await fetch(`/api/diff?${params.toString()}`);
        const payload = await response.json();
        if (loadToken !== activeLoadToken) {
            return;
        }
        if (!response.ok) {
            renderLoadError(state, payload.error || "Failed to load diff.");
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
        renderLoadError(state, error.message);
    }
}

function renderLoadError(state, message) {
    summaryGrid.replaceChildren();
    resultPanel.replaceChildren();
    resetHunkCaches();
    syncSelectedHunk(null);

    const box = document.createElement("div");
    box.className = "error-state";
    box.textContent = message;
    resultPanel.append(box);
    setStatus(message, true);
    closeActiveDiffStream();
}

function streamDiff(params, state, loadToken) {
    const stream = new EventSource(`/api/diff-stream?${params.toString()}`);
    activeDiffStream = stream;
    let streamState = null;
    let finished = false;
    const requestDetails = {
        mode: state.mode,
        query: params.toString(),
    };

    const fail = (message, error = null) => {
        if (loadToken !== activeLoadToken) {
            return;
        }
        console.error("[dirdiff] Diff stream failed", {
            ...requestDetails,
            message,
            error,
        });
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

    stream.addEventListener("stream-error", (event) => {
        if (finished) {
            return;
        }
        try {
            const payload = event.data ? JSON.parse(event.data) : null;
            fail(payload?.error || "Failed to stream diff.", payload);
        } catch (error) {
            fail("Failed to stream diff.", {
                cause: error,
                rawEvent: event,
            });
        }
    });

    stream.addEventListener("error", (event) => {
        if (finished) {
            return;
        }
        fail("Failed to stream diff.", {
            type: "transport",
            readyState: stream.readyState,
            rawEvent: event,
        });
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

function bindCommittedLoadInput(input) {
    input.addEventListener("change", () => scheduleLoadDiff(0));
    input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") {
            return;
        }
        event.preventDefault();
        scheduleLoadDiff(0);
    });
}

function splitRemoteQualifiedRef(ref, remoteNames) {
    const normalizedRef = (ref || "").trim();
    for (const remoteName of [...remoteNames].sort((left, right) => right.length - left.length)) {
        const prefix = `${remoteName}/`;
        if (normalizedRef.startsWith(prefix)) {
            return {
                remote: remoteName,
                value: normalizedRef.slice(prefix.length),
            };
        }
    }
    return {
        remote: "",
        value: normalizedRef,
    };
}

function qualifyRemoteRef(remote, ref, remoteNames) {
    const normalizedRemote = (remote || "").trim();
    const normalizedRef = (ref || "").trim();
    if (!normalizedRemote || !normalizedRef) {
        return normalizedRef;
    }
    if (
        normalizedRef.startsWith("refs/")
        || BUILTIN_SIDES.has(normalizedRef)
        || /^[0-9a-f]{7,40}$/i.test(normalizedRef)
        || normalizedRef.includes(":")
        || normalizedRef.includes("^")
        || normalizedRef.includes("~")
        || remoteNames.some((name) => normalizedRef === name || normalizedRef.startsWith(`${name}/`))
    ) {
        return normalizedRef;
    }
    return `${normalizedRemote}/${normalizedRef}`;
}

function getSelectedMode() {
    return modeInput.value || "files";
}

function getControlState() {
    const mode = getSelectedMode();

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
        const baseRemote = baseRemoteInput.value.trim();
        const branchRemote = branchRemoteInput.value.trim();
        const baseBranchValue = baseBranchInput.value.trim();
        const branchValue = branchInput.value.trim();
        if (!remoteNames.length) {
            return {
                valid: false,
                message: "Branch review needs at least one remote.",
            };
        }
        if (!baseRemote) {
            return {
                valid: false,
                message: "Pick a base remote.",
            };
        }
        if (!baseBranchValue) {
            return {
                valid: false,
                message: "Pick a base branch.",
            };
        }
        if (!branchRemote) {
            return {
                valid: false,
                message: "Pick a branch remote.",
            };
        }
        if (!branchValue) {
            return {
                valid: false,
                message: "Pick a branch to compare against the base branch.",
            };
        }
        const baseBranch = qualifyRemoteRef(baseRemote, baseBranchValue, remoteNames);
        const reviewBranch = qualifyRemoteRef(branchRemote, branchValue, remoteNames);
        return {
            valid: true,
            mode,
            left: "",
            right: "",
            baseBranch,
            reviewBranch,
            baseBranchLabel: baseBranchValue,
            reviewBranchLabel: branchValue,
        };
    }

    const [left, right] = MODE_TO_SIDES[mode] || MODE_TO_SIDES.files;
    return {
        valid: true,
        mode,
        left,
        right,
        baseBranch: "",
        reviewBranch: "",
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
        const baseBranch = state.baseBranchLabel || state.baseBranch || "master";
        const reviewBranch = state.reviewBranchLabel || state.reviewBranch;
        return `${reviewBranch} vs ${baseBranch}`;
    }
    return `${payload.left_label} vs ${payload.right_label}`;
}

function syncModeUI() {
    const mode = getSelectedMode();
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

function scrollToTop() {
    window.scrollTo({
        top: 0,
        behavior: getHunkScrollBehavior(),
    });
}

window.addEventListener("blur", () => {
    stopHunkHold();
});

topHunkBtn.addEventListener("click", scrollToTop);
bindHunkButton(prevHunkBtn, "prev");
bindHunkButton(nextHunkBtn, "next");

window.addEventListener("keydown", (event) => {
    if (shouldIgnoreHunkNavKeyEvent(event)) {
        return;
    }
    if (event.key === "Home") {
        event.preventDefault();
        scrollToTop();
    } else if (event.key === "n" && !event.shiftKey) {
        navigateHunk("next");
    } else if (event.key === "N") {
        navigateHunk("prev");
    }
});
window.addEventListener(
    "scroll",
    () => {
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
    remote_names: [],
};
const remoteNames = refChoices.remote_names || [];
const initialBaseBranchRef = search.get("base_branch") || defaults.base_branch || "";
const initialBranchRef = search.get("review_branch") || defaults.review_branch || "";
const initialBaseBranchParts = splitRemoteQualifiedRef(initialBaseBranchRef, remoteNames);
const initialBranchParts = splitRemoteQualifiedRef(initialBranchRef, remoteNames);
baseRemoteInput.value = initialBaseBranchParts.remote;
baseBranchInput.value = initialBaseBranchParts.value;
branchRemoteInput.value = initialBranchParts.remote;
branchInput.value = initialBranchParts.value;
const initialLeft = search.get("left") || defaults.left || "index";
const initialRight = search.get("right") || defaults.right || "worktree";
const initialMode = search.get("mode")
    || defaults.mode
    || inferMode(initialLeft, initialRight, branchInput.value, baseBranchInput.value)
    || "branch-review";
modeInput.value = initialMode;
if (initialMode === "refs") {
    leftRefInput.value = initialLeft;
    rightRefInput.value = initialRight;
}
syncModeUI();
setupDebugMenu();
attachAutocomplete(baseBranchInput, (query) => {
    const values = filterValues(
        listRemoteBranchChoices(refChoices, baseRemoteInput.value),
        query,
    );
    return values.length ? [["remote_branches", values]] : [];
});
attachAutocomplete(branchInput, (query) => {
    const values = filterValues(
        listRemoteBranchChoices(refChoices, branchRemoteInput.value),
        query,
    );
    return values.length ? [["remote_branches", values]] : [];
});
attachAutocomplete(baseRemoteInput, refChoices, ["remote_names"]);
attachAutocomplete(branchRemoteInput, refChoices, ["remote_names"]);
attachAutocomplete(leftRefInput, refChoices, ["builtins", "locals", "remotes"]);
attachAutocomplete(rightRefInput, refChoices, ["builtins", "locals", "remotes"]);

form.addEventListener("submit", (event) => {
    event.preventDefault();
});

for (const button of modeButtonRow.querySelectorAll(".mode-button")) {
    button.addEventListener("click", () => {
        const nextMode = button.dataset.mode;
        if (!nextMode || getSelectedMode() === nextMode) {
            return;
        }
        modeInput.value = nextMode;
        syncModeUI();
        scheduleLoadDiff(0);
    });
}
bindCommittedLoadInput(baseRemoteInput);
bindCommittedLoadInput(baseBranchInput);
bindCommittedLoadInput(branchRemoteInput);
bindCommittedLoadInput(branchInput);
bindCommittedLoadInput(leftRefInput);
bindCommittedLoadInput(rightRefInput);

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

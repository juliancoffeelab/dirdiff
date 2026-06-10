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
const debugMenu = document.getElementById("debugMenu");
const debugMenuToggle = document.getElementById("debugMenuToggle");
const debugMenuPanel = document.getElementById("debugMenuPanel");
const debugScrollToggle = document.getElementById("debugScrollToggle");
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
let hunkNavigator = null;
let debugScrollLog = [];
let debugScrollPanel = null;
let debugScrollBody = null;
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
};

function persistDebugSettings() {
    window.localStorage.setItem(DEBUG_SETTINGS_KEY, JSON.stringify({
        scrollDebug: debugState.scrollDebug,
    }));
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

function syncDebugMenuControls() {
    if (debugScrollToggle) {
        debugScrollToggle.checked = debugState.scrollDebug;
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

    debugMenuPanel.addEventListener("click", (event) => {
        event.stopPropagation();
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

class HunkNavigator {
    constructor(root, log = () => {}) {
        this.root = root;
        this.log = log;
        this.currentIndex = 0;

        const anchors = [...this.root.querySelectorAll(".hunk-anchor")];
        if (!anchors.length) {
            throw new Error("HunkNavigator requires at least one hunk anchor.");
        }

        anchors[0].classList.add("active-hunk");
        anchors[0].setAttribute("aria-current", "true");
        this.log(`action=init current=0 anchorCount=${anchors.length} scrollY=${Math.round(window.scrollY)}`);
    }

    scrollNext() {
        this.#scrollToHunk(1, "next");
    }

    scrollPrev() {
        this.#scrollToHunk(-1, "prev");
    }

    #scrollToHunk(direction, action) {
        const anchors = [...this.root.querySelectorAll(".hunk-anchor")];
        if (!anchors.length) {
            throw new Error("HunkNavigator lost all hunk anchors.");
        }

        if (this.currentIndex >= anchors.length) {
            this.currentIndex = anchors.length - 1;
        }

        this.currentIndex =
            (this.currentIndex + direction + anchors.length) % anchors.length;

        this.root.querySelectorAll(".active-hunk").forEach((node) => {
            node.classList.remove("active-hunk", "active-hunk-flash");
            node.removeAttribute("aria-current");
        });

        const row = anchors[this.currentIndex];
        row.classList.add("active-hunk");
        row.setAttribute("aria-current", "true");
        row.scrollIntoView({ block: "center", behavior: "instant" });
        row.classList.add("active-hunk-flash");
        window.setTimeout(() => {
            row.classList.remove("active-hunk-flash");
        }, 450);

        this.log(
            `action=${action} current=${this.currentIndex} anchorCount=${anchors.length} scrollY=${Math.round(window.scrollY)}`,
        );
    }
}

function installHunkNavigatorIfPossible() {
    if (hunkNavigator || !resultPanel.querySelector(".hunk-anchor")) {
        return;
    }
    hunkNavigator = new HunkNavigator(
        resultPanel,
        (message) => appendDebugScrollLog("hunkNav", message),
    );
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

function applyRowDecoration(codeEl, text, syntaxSpans, tokens) {
    codeEl.replaceChildren();
    renderSyntaxText(codeEl, text || " ", syntaxSpans);
    if (tokens && tokens.length > 0) {
        decorateTokenDiff(codeEl, tokens);
    }
}

function makeDiffSide(
    row,
    side,
    sideLabel,
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

    applyRowDecoration(codeEl, text || " ", syntaxSpans, tokens);

    sideEl.append(noEl, codeEl);
    return sideEl;
}

function makeDiffRow(
    row,
    leftLabel,
    rightLabel,
    markHunkAnchor = false,
) {
    const rowEl = document.createElement("div");
    rowEl.className = `diff-row ${row.status}`;
    if (markHunkAnchor) {
        rowEl.classList.add("hunk-anchor");
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
    );
    const rightSide = makeDiffSide(
        row,
        "right",
        rightLabel,
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

function appendRenderedRows(
    processedRows,
    rowsHost,
    leftLabel,
    rightLabel,
    renderPassId,
) {
    let lastRow = rowsHost.lastElementChild;
    let previousRowStatus = lastRow?.classList.contains("insert")
        || lastRow?.classList.contains("delete")
        || lastRow?.classList.contains("replace")
        ? "replace"
        : "equal";
    const rowsFragment = document.createDocumentFragment();

    for (const row of processedRows) {
        if (renderPassId !== activeRenderPass) {
            return;
        }

        if (row.status === "elided") {
            const foldBar = makeFoldBar(row.count, leftLabel, rightLabel, row.label);
            rowsFragment.append(foldBar);
            lastRow = foldBar;
            previousRowStatus = "equal";
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
            previousRowStatus = "equal";

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

        const rowNode = makeDiffRow(
            row,
            leftLabel,
            rightLabel,
            isChangedRowStatus(row.status) && !isChangedRowStatus(previousRowStatus),
        );
        rowsFragment.append(rowNode);
        lastRow = rowNode;
        previousRowStatus = row.status;
    }

    rowsHost.append(rowsFragment);
}

function renderSideBySide(
    rows,
    leftLabel,
    rightLabel,
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

    wrapper.append(header, rowsHost);
    appendRenderedRows(
        processedRows,
        rowsHost,
        leftLabel,
        rightLabel,
        renderPassId,
    );
    return {
        wrapper,
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

    const { wrapper } = renderSideBySide(
        rows,
        leftLabel,
        rightLabel,
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
    };
}

function makeNotebookCellCard(
    filePayload,
    cell,
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

    const sourceSection = makeNotebookSection(
        cell.source_rows,
        "Left source",
        "Right source",
        renderPassId,
        {
            heading: "Cell source",
            foldHints: cell.source_fold_hints || [],
            renderMode: cell.source_render_mode || null,
            truncatedRows: cell.source_truncated_rows || 0,
        },
    );
    card.append(sourceSection.host);

    if (cell.metadata_changed) {
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
    };
}

function makeNotebookFileCard(payload, renderPassId = activeRenderPass) {
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

    if (payload.summary.notebook_metadata_changed) {
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
            renderPassId,
        );
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
    };
}

function entryDisplayName(entry) {
    const pathCandidate = String(entry.right_path || entry.left_path || entry.display_name || "").trim();
    if (pathCandidate.includes(" -> ")) {
        return pathCandidate;
    }
    return pathCandidate || "(unknown)";
}

function isGeneratedLazyEntry(entry) {
    const path = String(entry.right_path || entry.left_path || "").trim().toLowerCase();
    return [
        "cargo.lock",
        "composer.lock",
        "flake.lock",
        "go.sum",
        "package-lock.json",
        "pdm.lock",
        "pipfile.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    ].some((name) => path.endsWith(`/${name}`) || path === name);
}

function makeFileCard(payload, renderPassId = activeRenderPass) {
    if (payload.render_kind === "notebook") {
        return makeNotebookFileCard(payload, renderPassId);
    }

    const card = document.createElement("article");
    card.className = "file-card";
    const body = document.createElement("div");
    body.className = "file-card-body";

    const title = document.createElement("h2");
    title.className = "file-title";
    const displayName = entryDisplayName(payload);
    title.textContent = displayName;

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
    const badgeNodes = [];
    if (payload.summary) {
        badgeNodes.push(
            badge(payload.summary.left_exists ? "left exists" : "left missing", "badge-neutral"),
            badge(payload.summary.right_exists ? "right exists" : "right missing", "badge-neutral"),
        );
    }
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
    if (payload.lazy) {
        badgeNodes.push(
            badge(
                isGeneratedLazyEntry(payload) ? "generated" : "loads on expand",
                "badge-neutral",
            ),
        );
    }
    badges.append(...badgeNodes);

    const headerActions = document.createElement("div");
    headerActions.className = "file-card-header-actions";
    headerActions.append(badges);

    const header = document.createElement("div");
    header.className = "file-card-header";
    header.append(titleWrap, headerActions);
    let hydrated = !payload.lazy;
    let hydratePromise = null;

    async function hydrateIfNeeded() {
        if (hydrated) {
            return;
        }
        if (!hydratePromise) {
            hydratePromise = (async () => {
                const loading = document.createElement("div");
                loading.className = "empty-state";
                loading.textContent = "Loading file diff…";
                body.replaceChildren(loading);
                try {
                    const nextPayload = await fetchFileDiff(payload);
                    payload.lazy = false;
                    payload.rows = nextPayload.rows || [];
                    payload.fold_hints = nextPayload.fold_hints || [];
                    payload.render_mode = nextPayload.render_mode || null;
                    payload.truncated_rows = nextPayload.truncated_rows || 0;
                    payload.summary = nextPayload.summary || payload.summary;
                    const { wrapper } = renderSideBySide(
                        payload.rows,
                        nextPayload.left_label,
                        nextPayload.right_label,
                        payload.fold_hints,
                        renderPassId,
                    );
                    header.setCollapsedBodyVisible?.(false);
                    body.replaceChildren(wrapper);
                    hydrated = true;
                    installHunkNavigatorIfPossible();
                } catch (error) {
                    const failure = document.createElement("div");
                    failure.className = "error-state";
                    failure.textContent = error instanceof Error
                        ? error.message
                        : "Failed to load file diff.";
                    body.replaceChildren(failure);
                }
            })();
        }
        await hydratePromise;
    }

    header.prepend(
        makeCollapsibleHeader(card, header, body, {
            expandedLabel: `Collapse file ${displayName}`,
            collapsedLabel: `Expand file ${displayName}`,
            beforeExpand: hydrateIfNeeded,
            startCollapsed: !!payload.lazy,
            showCollapsedBody: !!payload.lazy,
        }),
    );
    let lazyLoadButton = null;
    if (payload.lazy) {
        card.classList.add("file-card-lazy-generated");
        lazyLoadButton = document.createElement("button");
        lazyLoadButton.type = "button";
        lazyLoadButton.className = "file-lazy-load-toggle";
        const isGenerated = isGeneratedLazyEntry(payload);
        const lazyTitle = payload.change_type === "delete"
            ? "Load deleted file diff"
            : isGenerated
                ? "Load generated diff"
                : "Load diff";
        lazyLoadButton.innerHTML = [
            `<span class="file-lazy-load-toggle-title">${lazyTitle}</span>`,
            `<span class="file-lazy-load-toggle-meta">${displayName} is folded by default. Click to fetch and open it.</span>`,
        ].join("");
        lazyLoadButton.addEventListener("click", (event) => {
            event.stopPropagation();
            void header.click();
        });
    }
    card.append(header);
    if (payload.lazy) {
        if (lazyLoadButton) {
            body.append(lazyLoadButton);
        }
    } else {
        const renderResult = renderSideBySide(
            payload.rows,
            payload.left_label,
            payload.right_label,
            payload.fold_hints || [],
            renderPassId,
        );
        body.append(renderResult.wrapper);
    }
    card.append(body);

    return {
        card,
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
    hunkNavigator = null;
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
        payload.files.forEach((entry) => {
            if (entry.error) {
                repoView.appendEntry(entry, makeErrorCard(entry));
                return;
            }
            repoView.appendEntry(entry, makeFileCard(entry, renderPassId).card);
        });
        installHunkNavigatorIfPossible();
        return;
    }

    resultPanel.append(makeFileCard(payload, renderPassId).card);
    installHunkNavigatorIfPossible();
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
    if (entry.display_name) {
        params.set("display_name", entry.display_name);
    }
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

function beginRepoStream(initialPayload) {
    const renderPassId = ++activeRenderPass;
    hunkNavigator = null;
    resultPanel.replaceChildren();
    renderSummary(initialPayload.summary, initialPayload.mode);
    const repoView = makeRepoGroupView();
    resultPanel.append(repoView.controls, repoView.groupsHost);

    const payload = {
            ...initialPayload,
            files: [],
    };
    currentPayload = payload;
    return {
        payload,
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

    streamState.repoView.appendEntry(entry, makeFileCard(entry, streamState.renderPassId).card);
    installHunkNavigatorIfPossible();
}

let nextFileCardBodyId = 0;

function makeCollapsibleHeader(
    container,
    header,
    body,
    {
        indicatorClassName = "file-collapse-indicator",
        expandedLabel = "Collapse section",
        collapsedLabel = "Expand section",
        beforeExpand = null,
        startCollapsed = false,
        showCollapsedBody = false,
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
    let toggling = false;
    let collapsedBodyVisible = showCollapsedBody;

    function setExpanded(expanded) {
        body.hidden = !expanded && !collapsedBodyVisible;
        container.classList.toggle("is-collapsed", !expanded);
        header.setAttribute("aria-expanded", expanded ? "true" : "false");
        header.setAttribute("aria-label", expanded ? expandedLabel : collapsedLabel);
        indicator.textContent = expanded ? "▾" : "▸";
    }

    header.setExpanded = setExpanded;
    header.setCollapsedBodyVisible = (visible) => {
        collapsedBodyVisible = !!visible;
        if (container.classList.contains("is-collapsed")) {
            body.hidden = !collapsedBodyVisible;
        }
    };
    setExpanded(!startCollapsed);

    async function toggleExpanded() {
        if (toggling) {
            return;
        }
        const nextExpanded = container.classList.contains("is-collapsed");
        if (!nextExpanded) {
            setExpanded(false);
            return;
        }
        toggling = true;
        try {
            if (beforeExpand) {
                await beforeExpand();
            }
            setExpanded(true);
        } finally {
            toggling = false;
        }
    }

    header.addEventListener("click", () => {
        void toggleExpanded();
    });
    header.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
            return;
        }
        event.preventDefault();
        void toggleExpanded();
    });
    return indicator;
}

function setExpandablesExpanded(expandables, expanded) {
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

async function loadDiff() {
    return loadDiffWithOptions({});
}

async function loadDiffWithOptions() {
    const params = new URLSearchParams();
    const state = getControlState();
    if (!state.valid) {
        hunkNavigator = null;
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
    hunkNavigator = null;
    closeActiveDiffStream();
    const loadToken = ++activeLoadToken;

    try {
        if (typeof EventSource === "undefined") {
            throw new Error("This browser does not support streamed diffs.");
        }
        streamDiff(params, state, loadToken);
    } catch (error) {
        if (loadToken !== activeLoadToken) {
            return;
        }
        renderLoadError(state, error.message);
    }
}

function renderLoadError(state, message) {
    hunkNavigator = null;
    summaryGrid.replaceChildren();
    resultPanel.replaceChildren();

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
        hunkNavigator = null;
        summaryGrid.replaceChildren();
        resultPanel.replaceChildren();

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

window.addEventListener("keydown", (event) => {
    if (shouldIgnoreHunkNavKeyEvent(event) || !hunkNavigator) {
        return;
    }

    if (event.key === "n" && !event.shiftKey) {
        event.preventDefault();
        hunkNavigator.scrollNext();
    } else if (event.key === "N") {
        event.preventDefault();
        hunkNavigator.scrollPrev();
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

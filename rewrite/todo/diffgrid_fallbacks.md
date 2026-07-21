# DiffGrid fallback audit

This document preserves the deferred audit of fallback behavior in
`frontend/src/new/hud/DiffGrid.tsx`. It records the current code paths, their
contracts, consequences, and smallest prospective corrections so a later
rewrite pass can make explicit decisions without repeating the investigation.
It does not authorize implementation, change the renderer contract, or replace
the governing rewrite specifications and guidance.

The common governing rule for every potential fallback is:

> “If a function returned unexpectedly bad data, assert. If you want to add fallback, explicitly ask a user if fallback is acceptable.”

— [rewrite/stages/guidance.md:45](../stages/guidance.md#L45)

The same guidance also requires:

> “Assert required data at its boundary.”

— [rewrite/stages/guidance.md:146](../stages/guidance.md#L146)

## F40 — Contract decision: blank equal-row right text falls back to left text

Relevant code:

```tsx
function renderInlineDiffRowsDom(
  row: DiffRow,
  rowIndex: number,
  fileLabel: string,
  fileIndex: number,
  lineNumberState: InlineLineNumberState,
): DocumentFragment | HTMLElement {
  const rightText = sideText(row, "right");
  const leftText = sideText(row, "left");
  const sharedText = sharedSideText(leftText, rightText);
  const sharedTokens = sharedSideTokens(row);
  const sharedSyntax = sharedSideSyntax(row);

  switch (row.status) {
    case "equal":
      return renderInlineDiffRowDom({
        status: "equal",
        marker: " ",
        leftNo: row.left_no,
        rightNo: row.right_no,
        text: sharedText,
        tokens: sharedTokens,
        syntax: sharedSyntax,
        // ...
      });
```

```tsx
function sharedSideText(leftText: string, rightText: string): string {
  if (rightText.length > 0) {
    return rightText;
  }
  return leftText;
}
```

— [frontend/src/new/hud/DiffGrid.tsx:634](../../frontend/src/new/hud/DiffGrid.tsx#L634), [DiffGrid.tsx:795](../../frontend/src/new/hud/DiffGrid.tsx#L795)

Relevant contract:

> “Nullable side fields represent genuinely absent lines.”

— [frontend/src/new/api/api.ts:584](../../frontend/src/new/api/api.ts#L584)

The specification does not say which side is canonical when an inline `equal` row has different side text. It also does not state that `equal` requires identical text; structural engines can carry token-level changes on an otherwise `equal` row.

A row reaches this path in inline mode whenever `row.status === "equal"`. Both `null` right text and an explicitly present empty string become `""`; `sharedSideText` then substitutes the left text. The renderer therefore cannot distinguish “right side absent” from “right side is a real blank line.”

This is a fallback because it treats blank right text as unusable and silently substitutes another field. Consequences include showing old content where the resulting side is blank and applying independently selected decorations to the substituted text.

Likely reason, inferred from history: this behavior was copied unchanged from `v_old` to preserve visual parity and to obtain one visible line from two-sided equal rows.

Smallest correction requires a user contract choice:

1. Make right-side text canonical in inline equal rows, including `""`; or
2. Assert the precise allowed relationship between both equal-row sides before rendering.

## F41 — Confirmed: text, tokens, and syntax can come from different sides

Relevant code:

```tsx
const rightText = sideText(row, "right");
const leftText = sideText(row, "left");
const sharedText = sharedSideText(leftText, rightText);
const sharedTokens = sharedSideTokens(row);
const sharedSyntax = sharedSideSyntax(row);

switch (row.status) {
  case "equal":
    return renderInlineDiffRowDom({
      status: "equal",
      marker: " ",
      leftNo: row.left_no,
      rightNo: row.right_no,
      text: sharedText,
      tokens: sharedTokens,
      syntax: sharedSyntax,
      // ...
    });
}
```

```tsx
function sharedSideTokens(row: DiffRow): InlineToken[] {
  if (row.right_tokens.length > 0) {
    return row.right_tokens;
  }
  return row.left_tokens;
}

function sharedSideSyntax(row: DiffRow): SyntaxSpan[] {
  if (row.right_syntax.length > 0) {
    return row.right_syntax;
  }
  return row.left_syntax;
}
```

— [frontend/src/new/hud/DiffGrid.tsx:641](../../frontend/src/new/hud/DiffGrid.tsx#L641), [DiffGrid.tsx:808](../../frontend/src/new/hud/DiffGrid.tsx#L808)

Relevant contracts:

> “Consumers render the exact text and whitespace flag; they must not retokenize rows or infer another status.”

— [frontend/src/new/api/api.ts:549](../../frontend/src/new/api/api.ts#L549)

> “Empty means the line has no token-level decoration on that side.”

— [src/dirdiff/engines/base.py:238](../../src/dirdiff/engines/base.py#L238)

An inline equal row reaches this code. If its right text is non-empty but `right_tokens` is empty, the renderer selects right text and left tokens. Syntax is selected independently again, so one rendered line may combine three different choices.

The code substitutes left arrays for explicitly empty right arrays. That is a confirmed fallback because an empty array is already meaningful backend data—“no decoration”—rather than missing data. It can color the wrong character ranges, attach left-side syntax classes to right-side text, and make F48’s ignored token-text mismatch visible.

Likely reason, inferred from the old renderer: equal lines usually have identical text, so reusing whichever side carries decoration appeared harmless. That assumption is not asserted.

Smallest direct correction: select `{text, tokens, syntax}` atomically from one side. Which side is canonical depends on the F40 decision. If cross-side equality is required, assert it instead of selecting fields independently.

## F42 — Contract decision: status contradicting side data is silently ignored

Relevant code:

```tsx
switch (row.status) {
  case "delete":
    return renderInlineDiffRowDom({
      status: "delete",
      marker: "-",
      leftNo: row.left_no,
      rightNo: null,
      text: leftText,
      tokens: row.left_tokens,
      syntax: row.left_syntax,
      // ...
    });

  case "insert":
    return renderInlineDiffRowDom({
      status: "insert",
      marker: "+",
      leftNo: null,
      rightNo: row.right_no,
      text: rightText,
      tokens: row.right_tokens,
      syntax: row.right_syntax,
      // ...
    });
}
```

— [frontend/src/new/hud/DiffGrid.tsx:647](../../frontend/src/new/hud/DiffGrid.tsx#L647)

Relevant contract:

> “The value drives presentation only and must not be recomputed from text or line-number presence in the browser.”

— [frontend/src/new/api/api.ts:535](../../frontend/src/new/api/api.ts#L535)

The specification does not explicitly state the required side-field matrix for each status—for example, that delete rows must have no right line or that insert rows must have no left line.

The path is reached when a `delete` row nevertheless carries right text, number, tokens, or syntax, or an `insert` row carries corresponding left data. Inline rendering overwrites the opposite displayed number with `null` and never reads that side’s remaining content.

The fallback is acceptance of contradictory input followed by silent suppression. The UI looks internally consistent, but it conceals a backend/schema defect and can discard content or navigation-relevant information.

Likely reason, inferred: the renderer treats status as the presentation authority and was written assuming upstream engines always produce the conventional side shape.

Smallest correction requires choosing the contract, then asserting it at the decoded row boundary. The likely strict contract is:

- `delete`: right number/text absent and right decoration arrays empty;
- `insert`: left number/text absent and left decoration arrays empty.

If contradictory side data is intentionally permitted, the specification must define why it exists and why inline mode may discard it.

## F43 — Contract decision: malformed replace/move sides are omitted

Relevant code:

```tsx
case "replace": {
  const fragment = document.createDocumentFragment();
  const hasLeftSide = inlineSideExists(row.left_no, leftText);
  const hasRightSide = inlineSideExists(row.right_no, rightText);

  if (hasLeftSide) {
    fragment.append(
      renderInlineDiffRowDom({
        status: "delete",
        marker: "-",
        leftNo: row.left_no,
        rightNo: null,
        text: leftText,
        tokens: row.left_tokens,
        syntax: row.left_syntax,
        // ...
      }),
    );
  }

  if (hasRightSide) {
    fragment.append(
      renderInlineDiffRowDom({
        status: "insert",
        marker: "+",
        leftNo: null,
        rightNo: row.right_no,
        text: rightText,
        tokens: row.right_tokens,
        syntax: row.right_syntax,
        // ...
      }),
    );
  }
  return fragment;
}
```

```tsx
function inlineSideExists(lineNo: number | null, text: string): boolean {
  return lineNo !== null || text.length > 0;
}
```

The same predicate is used for `move`.

— [frontend/src/new/hud/DiffGrid.tsx:696](../../frontend/src/new/hud/DiffGrid.tsx#L696), [DiffGrid.tsx:834](../../frontend/src/new/hud/DiffGrid.tsx#L834)

Relevant contract:

> “Nullable side fields represent genuinely absent lines.”

— [frontend/src/new/api/api.ts:584](../../frontend/src/new/api/api.ts#L584)

The specification is silent on whether tokens or syntax may exist when both a side’s line number and text are absent.

A replace or move row reaches this path in inline mode. A side is omitted when its number is `null` and normalized text is empty, even if its token or syntax arrays are non-empty. If both sides meet that condition, the returned fragment contains no row at all. A row carrying `hunk_index` can then lose its required visible hunk target.

The fallback accepts incoherent side data and treats number/text as sufficient authority to suppress the entire side. The consequence is silently discarded decoration data, potentially missing rows, and interaction with F44 because an expanded fold can render an empty fragment.

Likely reason, inferred: empty one-sided fragments occur in structural diffs, and the predicate was intended to avoid rendering meaningless blank `-` or `+` rows while preserving legitimately numbered blank lines.

Smallest correction: assert side coherence before rendering:

- an absent side must have no number, no text, and empty decoration arrays;
- a present side must have a defined line identity or explicitly permitted unnumbered text;
- replace/move must produce at least one visible side.

Whether decoration arrays can establish presence is a contract choice.

## F44 — Confirmed: expanding a fold can remove its own collapse control

Relevant code:

```tsx
const renderFold = () => {
  const expanded = expandedFolds.has(rowIndex);
  if (expanded) {
    const fragment = renderSplitRowsDom(
      row.foldedRows,
      fileLabel,
      leftLabel,
      rightLabel,
      expandedFolds,
      fileIndex,
      row.startRow,
    );
    attachExpandedFoldToggle(fragment, toggle);
    wrapper.replaceChildren(fragment);
    return;
  }

  const button = document.createElement("button");
  button.type = "button";
  button.className = "diff-row fold-bar";
  button.title = "Expand folded rows";
  button.addEventListener("click", toggle);
  // ...
};
```

```tsx
function attachExpandedFoldToggle(
  fragment: DocumentFragment,
  onToggle: () => void,
) {
  const row = fragment.querySelector(
    ".diff-row:not(.fold-bar):not(.inline-fold-bar)",
  );
  if (!(row instanceof HTMLElement)) {
    return;
  }

  row.classList.add("fold-toggle-row", "fold-expanded");
  row.title = "Fold rows";
  row.addEventListener("click", onToggle);

  const lineNumber = row.querySelector(".line-no");
  if (lineNumber instanceof HTMLElement) {
    lineNumber.prepend(createFoldToggleButtonDom({ expanded: true, onToggle }));
  }
}
```

Inline expansion uses the same attachment function.

— [frontend/src/new/hud/DiffGrid.tsx:435](../../frontend/src/new/hud/DiffGrid.tsx#L435), [DiffGrid.tsx:568](../../frontend/src/new/hud/DiffGrid.tsx#L568)

Relevant specification:

> “File and directory expansion controls use ‘Collapse’ and ‘Expand’ in titles and accessible names. … This exception changes those strings only; it does not authorize different geometry, styling, placement, or behavior.”

— [rewrite/stages/guidance.md:98](../stages/guidance.md#L98)

The folded-lines specification describes hunk behavior but is silent on nested-fold control placement and whether a parent fold may be entirely covered by child folds. See [rewrite/spec/08_hunk_navigation.md:251](../spec/08_hunk_navigation.md#L251).

This path is reached when expanding a parent whose rendered descendants contain only nested fold bars, or when F43 causes all ordinary descendants to disappear. The selector deliberately excludes both kinds of fold bar, finds no ordinary row, and returns without attaching a parent collapse control.

The fallback is the silent `return` when the expected descendant does not exist. The user can no longer collapse that parent directly. This is a definite interaction defect rather than merely malformed decoration handling because the current fold parser permits nested ranges that can cover the parent.

Likely reason, inferred: the exclusion prevents a parent row click from also triggering a child fold-bar click. The original implementation assumed every expanded range retained at least one ordinary visible row.

Smallest direct correction: ensure the parent always gets a dedicated collapse button. If the first descendant is itself a fold bar, prepend only the parent button and let that button stop propagation; do not attach the parent’s row-level click listener to the child button row. Alternatively, explicitly reject fully covering nested fold structures at fold validation, but that changes the fold contract.

## F45 — Contract decision: missing `.line-no` silently loses keyboard disclosure

Relevant code:

```tsx
function attachExpandedFoldToggle(
  fragment: DocumentFragment,
  onToggle: () => void,
) {
  const row = fragment.querySelector(
    ".diff-row:not(.fold-bar):not(.inline-fold-bar)",
  );
  if (!(row instanceof HTMLElement)) {
    return;
  }

  row.classList.add("fold-toggle-row", "fold-expanded");
  row.title = "Fold rows";
  row.addEventListener("click", onToggle);

  const lineNumber = row.querySelector(".line-no");
  if (lineNumber instanceof HTMLElement) {
    lineNumber.prepend(createFoldToggleButtonDom({ expanded: true, onToggle }));
  }
}
```

Every current ordinary split and inline row constructs line-number cells:

```tsx
element.append(
  createLineNumberDom(/* ... */),
  createLineNumberDom(/* ... */),
  createInlineLineCodeDom(/* ... */),
);
```

— [frontend/src/new/hud/DiffGrid.tsx:574](../../frontend/src/new/hud/DiffGrid.tsx#L574), [DiffGrid.tsx:950](../../frontend/src/new/hud/DiffGrid.tsx#L950)

Relevant guidance:

> “Assert required data at its boundary.”

— [rewrite/stages/guidance.md:146](../stages/guidance.md#L146)

The specification is silent about the internal `.line-no` DOM invariant. It only requires fold-control terminology and unchanged behavior.

Under the current renderer this branch is not independently reachable: every ordinary row contains `.line-no`. It becomes reachable only after an internal renderer change, external DOM mutation during construction, or a future row shape that violates the implicit invariant.

The code keeps a mouse-click listener on the row but silently omits the actual button. That removes keyboard accessibility and visible disclosure while pretending expansion succeeded. It is fallback-shaped because it accepts missing required internal structure rather than asserting.

Likely reason, inferred: this was defensive DOM programming copied from `v_old`, intended to avoid crashing if markup changed.

Smallest correction: once an ordinary row has been found, require its `.line-no` element and throw if absent. F44 still needs separate handling because “no ordinary row” is a different condition.

## F46 — Confirmed: invalid decoration offsets are silently clamped

Relevant code:

```tsx
function decoratedParts(
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
) {
  if (!text || (!tokens.length && !syntax.length)) {
    return [];
  }

  const tokenNodes = tokenParts(tokens);
  const boundaries = new Set([0, text.length]);

  for (const token of tokenNodes) {
    boundaries.add(clamp(token.start, 0, text.length));
    boundaries.add(clamp(token.end, 0, text.length));
  }

  for (const span of syntax) {
    boundaries.add(clamp(span.start, 0, text.length));
    boundaries.add(clamp(span.end, 0, text.length));
  }

  const sortedBoundaries = [...boundaries].sort(
    (left, right) => left - right,
  );
  // ...
}
```

— [frontend/src/new/hud/DiffGrid.tsx:1399](../../frontend/src/new/hud/DiffGrid.tsx#L1399)

Relevant contract:

> “Offsets and classes are authoritative backend output.”

— [frontend/src/new/api/api.ts:563](../../frontend/src/new/api/api.ts#L563)

Relevant guidance:

> “If a function returned unexpectedly bad data, assert.”

— [rewrite/stages/guidance.md:45](../stages/guidance.md#L45)

Any row with tokens or syntax reaches this path. Negative offsets become zero; oversized offsets become `text.length`; reversed ranges may contribute normalized boundaries but then fail to decorate anything.

The renderer invents valid-looking bounds from invalid backend values. This hides corruption and can move syntax styling onto a different substring or silently discard a span. It is precisely the defensive normalization the guidance calls a fallback.

Likely reason, inferred from the function’s own JSDoc and copied history: clamping was added as defensive protection against malformed backend offsets and JavaScript slicing edge cases.

Smallest direct correction: validate every syntax interval before segmentation, requiring at least `0 <= start <= end <= text.length`, with a user decision on whether zero-length spans are permitted. Token bounds should be validated through F48 rather than clamped.

## F47 — Contract decision: uncovered text is classified as unchanged

Relevant code:

```tsx
const token = tokenNodes.find(
  (candidate) => start >= candidate.start && end <= candidate.end,
);

const syntaxClasses = syntax
  .filter((span) => start >= span.start && end <= span.end)
  .flatMap((span) => visibleSyntaxClasses(span.classes));

const classes = syntaxClasses.length
  ? ["ts-token", ...new Set(syntaxClasses)]
  : [];

const status = token === undefined ? "unchanged" : token.status;
const isWhitespace =
  token === undefined
    ? /^\s+$/.test(text.slice(start, end))
    : token.isWhitespace;
const leading = token === undefined ? false : token.leading;

parts.push({
  text: text.slice(start, end),
  classes,
  status,
  isWhitespace,
  leading,
});
```

— [frontend/src/new/hud/DiffGrid.tsx:1428](../../frontend/src/new/hud/DiffGrid.tsx#L1428)

Relevant backend contract:

> “Empty means the line has no token-level decoration on that side.”

— [src/dirdiff/engines/base.py:238](../../src/dirdiff/engines/base.py#L238)

The generic frontend schema does not state whether a non-empty token list must cover all text. The difftastic-specific contract does:

> “If present, the concatenated token text for a side must correspond to that side’s displayed row text.”

— [src/dirdiff/engines/difftastic/logic.py:42](../../src/dirdiff/engines/difftastic/logic.py#L42)

This path is normal when tokens are empty but syntax spans create segment boundaries: without an inline-diff token, the text is correctly treated as having no token-level change. It becomes fallback behavior when a non-empty token list covers only part of the row; the uncovered remainder is invented as `unchanged`.

The architectural consequence is that incomplete token payloads look valid and can under-report changes. However, simply removing the default would break legitimate syntax-only rows.

Likely reason: the merger must represent syntax-highlighted text even when inline-change tokens are absent, so it needs a neutral token status.

Smallest correction: keep `unchanged` for wholly empty token arrays, but assert that any non-empty token list concatenates to the complete row text. If F48 is corrected this way, F47 needs no independent rendering change.

## F48 — Confirmed: token text mismatches are ignored while lengths are reused

Relevant code:

```tsx
function tokenParts(tokens: InlineToken[]): TokenPart[] {
  let cursor = 0;

  return tokens.map((token, index) => {
    const start = cursor;
    const end = start + token.text.length;
    cursor = end;

    return {
      start,
      end,
      status: token.status,
      isWhitespace: token.is_ws,
      leading: token.is_ws && index === 0,
    };
  });
}
```

```tsx
const token = tokenNodes.find(
  (candidate) => start >= candidate.start && end <= candidate.end,
);

// The visible value comes from row text, not token.text.
parts.push({
  text: text.slice(start, end),
  classes,
  status,
  isWhitespace,
  leading,
});
```

— [frontend/src/new/hud/DiffGrid.tsx:1370](../../frontend/src/new/hud/DiffGrid.tsx#L1370), [DiffGrid.tsx:1428](../../frontend/src/new/hud/DiffGrid.tsx#L1428)

Relevant contracts:

> “Consumers render the exact text and whitespace flag.”

— [frontend/src/new/api/api.ts:549](../../frontend/src/new/api/api.ts#L549)

> “The concatenated token text for a side must correspond to that side’s displayed row text.”

— [src/dirdiff/engines/difftastic/logic.py:42](../../src/dirdiff/engines/difftastic/logic.py#L42)

A decorated row reaches this code. Token strings establish cumulative interval lengths, but the strings themselves are discarded. If token text has the same length as the row substring but different characters, the mismatch is completely invisible. If lengths differ, F47 or F46-like boundary behavior follows.

The code accepts token text as geometry while rendering another source as content. It substitutes `text.slice(...)` for the token’s declared exact text without verifying equivalence. The consequence is incorrect token coloring attached to unrelated characters and a hidden backend invariant violation.

Likely reason, inferred: syntax offsets are defined against the row text, so the merger chose row text as the single visible source and used tokens only to build overlapping ranges.

Smallest direct correction: before constructing intervals, assert:

```tsx
tokens.length === 0 ||
  tokens.map((token) => token.text).join("") === text
```

The assertion should occur at the semantic row boundary if possible, not as recovery inside DOM generation.

## F49 — Rejected: undecorated text rendered as a native text node

Relevant code:

```tsx
function appendDecoratedText(
  element: HTMLElement,
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
  rowStatus: RowStatus,
) {
  const parts = decoratedParts(text, tokens, syntax);

  if (parts.length === 0) {
    element.append(text);
    return;
  }

  for (const part of parts) {
    const tokenChanged = part.status !== "unchanged";

    if (part.classes.length === 0 && !tokenChanged) {
      element.append(part.text);
      continue;
    }

    const span = document.createElement("span");
    const classes = [...part.classes];
    // Apply only actual syntax or change classes.
    span.className = classes.join(" ");
    span.textContent = part.text;
    element.append(span);
  }
}
```

— [frontend/src/new/hud/DiffGrid.tsx:1247](../../frontend/src/new/hud/DiffGrid.tsx#L1247)

Relevant specification:

> “At the same viewport, URL, backend data and UI state, every surface and behavior … must be a pixel-perfect 1:1 copy of its `v_old` counterpart.”

— [rewrite/stages/guidance.md:66](../stages/guidance.md#L66)

The specification is silent on whether undecorated substrings must be wrapped in `<span>` elements.

Rows with no tokens and no syntax return no parts and append their text directly. Within decorated rows, segments with neither syntax classes nor token changes are also appended directly.

Nothing is substituted, suppressed, or invented: the exact text is preserved as native searchable DOM content. Therefore this is not a fallback. It also mirrors `v_old` and produces the same visible result with less DOM.

Likely reason: avoid unnecessary spans while preserving native selection and browser search.

Smallest correction: none. If a future DOM-level contract requires every segment to have an element, that requirement should be specified first.

## F50 — Rejected: an empty fold label intentionally displays only the line count

Relevant code:

```tsx
function createFoldSideDom(
  count: number,
  label: string,
  sideLabel: string,
): HTMLElement {
  const element = document.createElement("div");
  element.className = "diff-side fold-side";
  element.dataset.sideLabel = sideLabel;

  element.append(
    createPlainLineNumberDom(".."),
    createElementWithClass(
      "div",
      "fold-label",
      label
        ? `... ${foldLineText(count)} in ${label}`
        : `... ${foldLineText(count)}`,
    ),
  );

  return element;
}
```

```tsx
function foldLabel(row: FoldRow): string {
  const lineText = foldLineText(row.count);
  if (row.label.length > 0) {
    return `... ${lineText} in ${row.label}`;
  }
  return `... ${lineText}`;
}
```

— [frontend/src/new/hud/DiffGrid.tsx:1088](../../frontend/src/new/hud/DiffGrid.tsx#L1088), [DiffGrid.tsx:1125](../../frontend/src/new/hud/DiffGrid.tsx#L1125)

Relevant contract:

```tsx
const FoldHintSchema = z.strictObject({
  start_row: z.number().int(),
  end_row: z.number().int(),
  kind: z.enum([
    "function_like",
    "class_like",
    "container",
    "section",
    "top_level",
  ]),
  label: z.string(),
});
```

— [frontend/src/new/api/api.ts:592](../../frontend/src/new/api/api.ts#L592)

The backend deliberately produces `label = ""` when neither visible context nor a captured structural label exists. See [src/dirdiff/rendering/fold.py:704](../../src/dirdiff/rendering/fold.py#L704).

The specification is silent on empty-label wording. It only requires fold-control terminology and visual parity.

A valid fold hint with `label: ""` reaches this branch. The renderer shows `... N lines` and omits the otherwise dangling `in ` suffix. It does not replace a malformed required label: the current backend and schema explicitly permit empty strings.

This therefore does not qualify as a forbidden fallback. The consequence is a reduced but complete presentation containing the important folded-line count.

Likely reason: some fold candidates have no meaningful structural name, and `... N lines in ` would be visibly broken.

Smallest correction: none. If labels are intended to become non-empty invariants, that must first be changed and asserted in the backend/API contract.

## Interactions

- **F40 + F41 + F48:** independent side selection can pair right text with left token lengths, after which F48 applies those lengths without checking token text. Resolving F40’s canonical-side contract and selecting all three fields atomically resolves F41; exact concatenation validation then resolves F48.

- **F43 + F44:** a malformed replace/move row can emit an empty fragment. If it is the only ordinary descendant of an expanded fold, F44 silently removes the fold’s collapse path.

- **F46 + F48:** both are semantic validation gaps. A single row-decoration boundary validator could assert token concatenation and syntax ranges before the DOM renderer receives them.

- **F47 + F48:** F47 should remain valid for empty token arrays. Once non-empty arrays must exactly match the complete row text under F48, the problematic partial-coverage case disappears.

## Decisions needed before implementation

- F40: canonical right side, or assert cross-side relationship.
- F42: strict status/side matrix, or explicitly allow contradictory fields.
- F43: assert absent-side coherence, or allow decorations to establish presence.
- F45: assert `.line-no` as an internal invariant.
- Confirm whether to address the definite findings F41, F44, F46, and F48.

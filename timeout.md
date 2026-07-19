1. The empty square is a bug.

`AppHeaderFileStatus.visible()` returns `true` whenever any file fetch is active. During an explicit fetch there may be:

- no automatic-progress fraction;
- no existing failure;
- no slow indicator yet.

The status group therefore mounts with no children, producing that empty bordered box. It should mount only when at least one compact indicator is actually visible.

2. The slow-file tooltip is unnecessarily custom.

Its text inherits this generic rule:

```css
.summary-group-status span {
  color: #6a665d;
}
```

That overrides the intended light tooltip text, producing dark text on a dark background. Image 3 is a native browser `title` tooltip. The clock should use the same thing:

```tsx
<button
  aria-label={message}
  title={message}
>
  <Clock3 aria-hidden="true" />
</button>
```

The custom tooltip element and CSS should be removed.

3. Retry currently reuses the original timed query.

Therefore a file retry still has the normal 8-second timeout, or 20 seconds for Difftastic/GumTree. That is wrong under your rule.

The policy should be:

- automatic/initial attempts retain their bounded timeout;
- every user-controlled `RetryButton` attempt has no HTTP timeout;
- `RetryButton` remains presentation-only;
- its owner explicitly starts an unbounded attempt;
- timeout policy does not enter the query key because it is not data identity.

This needs corresponding corrections in the status, error/retry, and TanStack specifications before implementation. No files changed.

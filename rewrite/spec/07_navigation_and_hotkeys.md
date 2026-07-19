## 66. Hotkeys, HUD placement, and browser selection

Hunk identity, NavigationProvider, NavigationCommand, selection, counters, scrolling, and FileTree navigation are specified in [08_hunk_navigation.md](08_hunk_navigation.md).

This topic retains only direct hotkeys, Help and Debug state, HUD placement, browser text-side selection, and ChangeSet composition.

### 66.20 Browser text-side selection

Browser left/right text selection remains outside Navigation.

It belongs with DiffGrid interaction:

```html
<div
  class="diff-grid"
  data-diff-selection-side="left"
></div>
```

It uses one delegated pointer handler and no Solid signal.

It is independent from:

- hunk selection;
- line pins;
- inline/split workspace view.

### 66.21 HintHud and DebugHud source placement

`HintHud` and `DebugHud` are defined beside each other in `ChangeSet.tsx`:

```text
function HintHud(...)
function DebugHud(...)
function DebugMetric(...)

...

function HelpModal(...)
```

`HelpModal` may use its own private supporting components after its definition. It does not split the two HUD component definitions.

### 66.22 HintHud

The existing three-button visual component remains:

```tsx
type HintHudProps = {
  helpOpen: boolean;
  onToggleHelp: () => void;
};
```

```tsx
function HintHud(
  props: HintHudProps,
) {
  const navigation = useNavigation();
  const toast = useToasts();

  return (
    <nav
      class="hint-hud"
      aria-label="Hunk navigation"
    >
      <button
        type="button"
        onClick={() =>
          void navigation
            .navigate({ kind: "next-hunk" })
            .catch((error) =>
              toast.showError(
                "Navigation failed",
                error,
              )
            )
        }
        title="Next hunk (n)"
      >
        Next <kbd>n</kbd>
      </button>

      <button
        type="button"
        onClick={() =>
          void navigation
            .navigate({ kind: "previous-hunk" })
            .catch((error) =>
              toast.showError(
                "Navigation failed",
                error,
              )
            )
        }
        title="Previous hunk (N)"
      >
        Prev <kbd>N</kbd>
      </button>

      <button
        type="button"
        aria-expanded={props.helpOpen}
        onClick={props.onToggleHelp}
        title="Hotkey help (h)"
      >
        Help <kbd>h</kbd>
      </button>
    </nav>
  );
}
```

Next and Previous use Navigation.

Help remains an explicit callback because Help visibility is not navigation.

Every HintHud navigation Promise is handled at this UI boundary. A rejection produces one persistent “Navigation failed” Toast and no `unhandledrejection`.

### 66.23 Help and Debug state

Help and Debug remain independent ChangeSet-owned values:

```ts
const [helpOpen, setHelpOpen] =
  createSignal(false);
```

```ts
const [debugOpen, setDebugOpen] =
  createSignal(false);
```

They are not variants of one union and are not grouped into shared HUD state.

Debug FPS, node, and span sampling runs only during the `DebugHud` lifetime:

```tsx
<Show when={debugOpen()}>
  <DebugHud
    globalSelectedHunk={() =>
      hunkDisplay().globalSelectedHunk
    }
  />
</Show>
```

Closed Debug performs no RAF sampling. Its Hunk value comes from the mounted ChangeSet shell's `HunkDisplay.globalSelectedHunk`; DebugHud performs no separate hunk DOM count.

Help remains an overlay under `hud/`.

### 66.24 Hotkeys

Hotkeys are direct browser input bindings.

There is no intermediate `Command` or `Hotkey` union.

A private lifecycle component in `ChangeSet.tsx` owns the single active hotkey listener:

```ts
type HotkeysProps = {
  onToggleTree: () => void;
  onToggleView: () => void;
  onReload: () => void;
  onToggleHelp: () => void;
  onToggleDebug: () => void;
};
```

It receives concrete callbacks rather than a grouped interface.

ChangeSet reload intentionally has no dedicated visible control. `R` is its only standing reload binding. An error-state `RetryButton` may still invoke reload as the explicit retry action.

```tsx
function Hotkeys(
  props: HotkeysProps,
) {
  const navigation = useNavigation();
  const toast = useToasts();

  onMount(() => {
    function onKeyDown(
      event: KeyboardEvent,
    ): void {
      if (shouldIgnoreHotkey(event)) {
        return;
      }

      if (
        event.code === "KeyN" &&
        !event.shiftKey
      ) {
        event.preventDefault();

        void navigation
          .navigate({ kind: "next-hunk" })
          .catch((error) =>
            toast.showError(
              "Navigation failed",
              error,
            )
          );

        return;
      }

      if (
        event.code === "KeyN" &&
        event.shiftKey
      ) {
        event.preventDefault();

        void navigation
          .navigate({ kind: "previous-hunk" })
          .catch((error) =>
            toast.showError(
              "Navigation failed",
              error,
            )
          );

        return;
      }

      if (event.code === "KeyP") {
        event.preventDefault();

        void navigation
          .navigate({ kind: "top" })
          .catch((error) =>
            toast.showError(
              "Navigation failed",
              error,
            )
          );

        return;
      }

      if (event.code === "KeyT") {
        event.preventDefault();
        props.onToggleTree();
        return;
      }

      if (event.code === "KeyI") {
        event.preventDefault();
        props.onToggleView();
        return;
      }

      if (event.code === "KeyR") {
        event.preventDefault();
        props.onReload();
        return;
      }

      if (event.code === "KeyD") {
        event.preventDefault();
        props.onToggleDebug();
        return;
      }

      if (event.code === "KeyH") {
        event.preventDefault();
        props.onToggleHelp();
      }
    }

    document.addEventListener(
      "keydown",
      onKeyDown,
    );

    onCleanup(() => {
      document.removeEventListener(
        "keydown",
        onKeyDown,
      );
    });
  });

  return null;
}
```

This code is intentionally direct.

The mappings are:

| Key | Operation |
|---|---|
| `n` | `navigation.navigate({ kind: "next-hunk" })` |
| `N` | `navigation.navigate({ kind: "previous-hunk" })` |
| `p` | `navigation.navigate({ kind: "top" })` |
| `t` | toggle ChangeSet FileTree |
| `i` | toggle workspace inline/split view |
| `r` | reload ChangeSet |
| `d` | toggle Debug |
| `h` | toggle Help |

### 66.25 Ignored hotkeys

One predicate protects ordinary input behavior:

```ts
function shouldIgnoreHotkey(
  event: KeyboardEvent,
): boolean {
  if (
    event.defaultPrevented ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey
  ) {
    return true;
  }

  const target = event.target;

  if (!(target instanceof HTMLElement)) {
    return false;
  }

  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}
```

Shift is not rejected because `N` uses it.

The hotkey handler calls `preventDefault()` only after recognizing a supported hotkey.

Navigation may separately observe native browser scrolling keys to identify user scroll intent. That observer does not map application hotkeys and does not prevent their default behavior.

### 66.26 No generic hotkey dispatch

Buttons call the concrete operation directly:

```text
HintHud Next
    → navigation.navigate

FileTree row
    → navigation.navigate

Header view control
    → workspace view setter

Help button
    → setHelpOpen

Debug button
    → setDebugOpen
```

The keyboard listener calls the same operations.

The FileTree line describes the eventual direct route only. FileTree rows remain inert until the separate design gate in [08_hunk_navigation.md](08_hunk_navigation.md) is approved and implemented; this section does not authorize that interaction.

There is no central bus between the user interaction and the concrete operation.

### 66.27 ChangeSet composition

```tsx
export function ChangeSet(
  props: ChangeSetProps,
) {
  const [helpOpen, setHelpOpen] =
    createSignal(false);

  const [debugOpen, setDebugOpen] =
    createSignal(false);

  let root!: HTMLElement;

  function toggleHelp(): void {
    setHelpOpen((open) => !open);
  }

  function toggleDebug(): void {
    setDebugOpen((open) => !open);
  }

  return (
    <section
      ref={root}
      data-change-set-root
    >
      <NavigationProvider
        root={() => root}
      >
        <Hotkeys
          onToggleTree={toggleTree}
          onToggleView={props.onToggleView}
          onReload={reload}
          onToggleHelp={toggleHelp}
          onToggleDebug={toggleDebug}
        />

        <ChangeSetTitle />

        <FileTree
          selectedFileIndex={() =>
            hunkDisplay().selectedFileIndex
          }
        />

        <FileCards />

        <div class="hud-stack">
          <Show when={debugOpen()}>
            <DebugHud
              globalSelectedHunk={() =>
                hunkDisplay().globalSelectedHunk
              }
            />
          </Show>

          <HintHud
            helpOpen={helpOpen()}
            onToggleHelp={toggleHelp}
          />
        </div>

        <HelpModal
          open={helpOpen()}
          onClose={() =>
            setHelpOpen(false)
          }
        />
      </NavigationProvider>
    </section>
  );
}
```

The actual ChangeSet also renders the previously specified Header Portal contributions and error boundaries. They are omitted from this example because they do not interact with Navigation.

### 66.28 Show-all and fold-all removal

The rewrite removes the current `s` and `f` whole-file hotkeys:

```text
s → Show all files
f → Fold all files
```

It also removes the corresponding `ChangeSetTitle` controls and Help rows. The three-button `HintHud` remains visually unchanged.

Those aggregate operations do not follow naturally from the new FileCard/ChangeSet ownership model and do not survive merely for compatibility.

There are no replacement callbacks, dead key branches, compatibility handlers, unused variants, or invisible retained behavior.

### 66.29 Specification terminology corrections

Rename:

```ts
PageNavigationCommand
```

to:

```ts
NavigationCommand
```

Rename the scroll-source member:

```ts
"command"
```

to:

```ts
"navigation"
```

Section 60 is named:

```text
Hotkeys
```

There is no:

```ts
type Command;
type NavigationCommands;
type ChangeSetCommands;
type WorkspaceCommands;
type HudCommands;

function commandForKey(...);
function dispatchCommand(...);
```

The only remaining `Command` terminology is `NavigationCommand`.

### 66.30 Required invariants

1. Exactly one application hotkey listener is mounted.
2. Inactive ChangeSets have no hotkey listener.
3. Hotkeys contain no generic command or dispatch abstraction.
4. Hotkeys ignored inside editable controls preserve native behavior.
5. Recognized hotkeys call `preventDefault()` before invoking their operation.
6. Help and Debug remain independent state values.
7. Closed Debug performs no sampling.
8. Browser text-side selection remains independent from Navigation.
9. `NavigationCommand` is the only surviving application command type.
10. `HintHud` and `DebugHud` definitions remain adjacent in source.
11. `HintHud` and `DebugHud` remain adjacent inside the rendered HUD stack.
12. `HelpModal` remains outside the HUD stack and never separates `HintHud` from `DebugHud`.
13. `ChangeSetTitle` contains no Show All or Fold All controls.
14. Help contains no Show All or Fold All rows.
15. `s` and `f` are not application hotkeys.

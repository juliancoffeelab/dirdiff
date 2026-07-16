## 66. Navigation and hotkeys clarification

### 66.1 Terminology

`navigation` means the subsystem responsible for:

- hunk selection;
- hunk traversal;
- main-page scrolling;
- user-scroll following;
- FileTree hunk destinations;
- line pins and their repeated restoration.

`NavigationCommand` is the explicit typed input to the main navigation gateway.

Hotkeys are direct keyboard bindings. They are not commands, a command system, or a generic dispatch framework.

There will be no:

- generic `Command` type;
- `CommandProvider`;
- `CommandRouter`;
- `dispatchCommand`;
- command registry;
- owner registration;
- `HudActions`;
- command metadata framework;
- `commands.ts`.

`hud/` remains the directory containing project-aware interface code. It is not a shared runtime owner.

### 66.2 Files

Navigation lives in:

```text
frontend/src/hud/navigation.tsx
```

The `.tsx` extension is required because the module contains `NavigationProvider`.

There will be no:

```text
hud/HunkNavigation.tsx
hud/Hud.tsx
hud/commands.ts
```

`navigation.tsx` exports:

```ts
export type RealHunkIdentity;
export type PseudoHunkIdentity;
export type HunkIdentity;
export type NavigationCommand;
export type Navigation;

export function NavigationProvider(
  props: NavigationProviderProps,
): JSX.Element;

export function useNavigation(): Navigation;
```

Hotkey handling remains private to `ChangeSet.tsx`.

`HintHud` and `DebugHud` are private components in `ChangeSet.tsx`. Their definitions are adjacent, and their rendered elements are adjacent inside the HUD stack.

`HelpModal` is also private to `ChangeSet.tsx`, but it is defined separately and rendered outside the HUD stack. It is not interspersed between `HintHud` and `DebugHud` in either source or rendered TSX.

### 66.3 Hunk identities

Real hunks retain the backend-produced file-local identity:

```ts
export type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  hunkIndex: number;
};
```

HuskFile and LazyFile pseudo-hunks retain their provisional identity:

```ts
export type PseudoHunkIdentity = {
  fileIndex: number;
  kind: "husk" | "lazy";
  entryDirection: 1 | -1;
};
```

```ts
export type HunkIdentity =
  | RealHunkIdentity
  | PseudoHunkIdentity;
```

`entryDirection` determines how a selected pseudo-hunk maps when its resulting FullFile becomes available:

- `1` maps to the first participating real hunk;
- `-1` maps to the last participating real hunk;
- direct FileTree or plank entry uses `1`.

Global positions such as `9/42` remain derived display values. They are never identity.

### 66.4 NavigationCommand

The only application-level use of the word `Command` is:

```ts
export type NavigationCommand =
  | { kind: "next-hunk" }
  | { kind: "previous-hunk" }
  | {
      kind: "hunk";
      hunk: HunkIdentity;
    }
  | { kind: "top" };
```

`NavigationCommand` exists because it is the explicit typed argument to the single ordinary-navigation gateway:

```ts
await navigation.navigate({
  kind: "next-hunk",
});
```

It does not represent:

- hotkeys;
- view changes;
- Help;
- Debug;
- reload;
- tree visibility;
- file expansion;
- backend work.

There are no file or directory navigation commands.

### 66.5 Public Navigation interface

```ts
export type Navigation = {
  navigate(
    command: NavigationCommand,
  ): Promise<void>;

  selectHunk(
    hunk: HunkIdentity,
  ): void;

  clearHunkSelection(): void;
};
```

`navigate` is the only ordinary hunk-navigation operation that moves the main page viewport.

`selectHunk` changes DOM selection and projections without scrolling.

`clearHunkSelection` clears DOM selection and projections without scrolling.

The interface does not expose:

- the ChangeSet root;
- controller state;
- scroll-source state;
- listener handles;
- hunk targets;
- selected identity;
- counters;
- line-pin timers;
- generic setters.

### 66.6 NavigationProvider purpose

`NavigationProvider` owns one stateful, disposable navigation controller for one mounted ChangeSet.

It gives descendants:

1. the same navigation instance;
2. operations already bound to the correct ChangeSet root;
3. one shared scroll-follow and line-pin lifecycle.

Without Context, ChangeSet would pass the same instance explicitly:

```tsx
<FileTree navigation={navigation} />
<FileCards navigation={navigation} />
<HintHud navigation={navigation} />
<Hotkeys navigation={navigation} />
```

With Context:

```tsx
<NavigationProvider root={() => root}>
  <FileTree />
  <FileCards />
  <HintHud />
  <Hotkeys />
</NavigationProvider>
```

Each consumer receives the nearest ChangeSet navigation instance:

```ts
const navigation = useNavigation();
```

Context changes delivery only. It does not make navigation global and does not move navigation truth out of the DOM.

### 66.7 Provider props

```ts
export type NavigationProviderProps = {
  root: Accessor<HTMLElement>;
  children: JSX.Element;
};
```

The root is required.

`NavigationProvider` must assert that the root exists when mounting its controller. It must not silently operate against `document` when the ChangeSet root is unavailable.

The Provider accepts no:

- workspace operations;
- Help operations;
- Debug operations;
- ChangeSet operations;
- hotkey definitions;
- backend data;
- optional handlers.

### 66.8 Navigation controller state

The controller is stateful.

Its state is imperative and ephemeral rather than reactive application state.

Conceptually:

```ts
type NavigationScrollSource =
  | "idle"
  | "user"
  | "navigation";
```

```ts
type PinRestorationState = {
  retryTimer: number | null;
  restoredKey: string;
};
```

```ts
type NavigationControllerState = {
  root: HTMLElement;

  scrollSource: NavigationScrollSource;

  followFrame: number | null;

  pinRestoration: PinRestorationState;
};
```

The actual line-pin implementation may require additional retry or stabilization handles. Those remain private controller fields.

The controller also owns the browser listener and observer cleanup associated with that state.

It does not use Solid signals for:

- `scrollSource`;
- `followFrame`;
- retry timers;
- the selected hunk;
- hunk counters.

These values do not drive ordinary JSX rendering.

### 66.9 Why the controller state is shared

Programmatic navigation and user-scroll following must coordinate through the same `scrollSource`.

Programmatic navigation:

```text
navigate(...)
    │
    ▼
scrollSource = "navigation"
    │
    ▼
select target
    │
    ▼
possibly waitToEnrich
    │
    ▼
scroll viewport
    │
    ▼
scroll events occur
    │
    ▼
scroll-follow recognizes navigation-owned movement
    │
    ▼
final settled frame
    │
    ▼
scrollSource = "idle"
```

Natural user scrolling:

```text
wheel, touch or native scrolling key
    │
    ▼
scrollSource = "user"
    │
    ▼
many scroll events occur
    │
    ▼
one shared followFrame throttles DOM traversal
    │
    ▼
scrollend
    │
    ▼
final follow sample
    │
    ▼
scrollSource = "idle"
```

Without one controller instance, these paths would still require shared mutable state somewhere.

The Provider gives that state one explicit ChangeSet-scoped lifetime.

### 66.10 Navigation truth remains in DOM and URL

The controller does not become a navigation store.

| Information | Authority |
|---|---|
| selected hunk identity | attributes on the owning FileCard |
| participating target order | current DOM order |
| visible selected decoration | current target DOM |
| local/global hunk counters | imperative DOM projection |
| FileTree highlight | projection from selected FileCard |
| rich/virtual mode | FullFile-local Solid state |
| FileCard expansion | ChangeSet-owned Solid state |
| line-pin identity | URL |
| line-pin visual highlight | rendered DOM |
| scroll coordination | Navigation controller |
| line-pin retries | Navigation controller |

The controller may temporarily hold DOM references during one operation. It must not retain a second authoritative hunk registry.

### 66.11 NavigationProvider lifecycle

The Provider is mounted only with active ChangeSet content.

On mount it:

1. resolves and asserts the ChangeSet root;
2. constructs one controller;
3. attaches navigation-related browser listeners;
4. starts line-pin restoration when a URL pin exists;
5. exposes the controller’s public Navigation operations through Context.

On cleanup it:

- cancels the scheduled scroll-follow frame;
- cancels every line-pin timer and retry;
- removes wheel, touch, scroll, scrollend, hash and pointer listeners;
- disconnects navigation-owned observers;
- marks the controller disposed;
- permits no later scheduled callback to mutate DOM or scroll.

Inactive ChangeSets retain their small outer component state, but their expensive active content and NavigationProvider are unmounted.

Therefore there is exactly one active:

- navigation controller;
- user-scroll follower;
- line-pin restoration controller;
- hotkey listener.

### 66.12 Context accessor

```ts
const NavigationContext =
  createContext<Navigation>();
```

```ts
export function useNavigation(): Navigation {
  const navigation =
    useContext(NavigationContext);

  if (navigation === undefined) {
    throw new Error(
      "useNavigation requires NavigationProvider.",
    );
  }

  return navigation;
}
```

`useNavigation()` is a checked Solid Context accessor.

It does not:

- create another controller;
- subscribe to navigation state;
- return a setter;
- copy DOM state into Solid;
- install listeners.

### 66.13 Hunk selection

The Provider-bound operation is equivalent to:

```ts
function selectHunk(
  root: HTMLElement,
  hunk: HunkIdentity,
): void;
```

It:

1. resolves the current target for the identity;
2. removes selected identity from the previous FileCard;
3. removes previous target decoration;
4. writes selected identity onto the new owning FileCard;
5. decorates the resolved target;
6. updates local and global counters;
7. updates FileTree highlighting;
8. does not scroll.

```ts
function clearHunkSelection(
  root: HTMLElement,
): void;
```

It:

- removes selected identity;
- removes selected decoration;
- clears FileTree highlighting;
- updates counters;
- does not choose a replacement;
- does not scroll.

Only the previously approved paths may select or reselect a hunk:

1. Next/Previous navigation.
2. FileTree navigation to a file’s first hunk.
3. recognized user-scroll following;
4. Husk or Lazy plank activation;
5. destructive structural repair;
6. explicitly approved future line-pin selection behavior.

Line-pin restoration currently does not select.

### 66.14 Main navigation gateway

The Provider-bound `navigate` operation is equivalent to:

```ts
async function navigate(
  root: HTMLElement,
  command: NavigationCommand,
): Promise<void>;
```

For Next and Previous:

```text
read selected identity from FileCard DOM
    │
    ▼
query participating targets in DOM order
    │
    ▼
choose adjacent target with wrapping
    │
    ▼
selectHunk
    │
    ▼
waitToEnrich when real geometry is required
    │
    ▼
resolve the target again
    │
    ▼
perform one main-page scroll
```

For a direct hunk:

```text
resolve supplied HunkIdentity
    │
    ▼
selectHunk
    │
    ▼
waitToEnrich when required
    │
    ▼
resolve again
    │
    ▼
perform one main-page scroll
```

For Top:

```text
preserve selected identity
    │
    ▼
scroll main viewport to zero
```

Outside line-pin restoration, no other code may call main-page:

- `window.scrollTo`;
- `scrollIntoView`;
- `scrollBy`.

### 66.15 Pseudo-hunks

Next and Previous traverse the current provisional target sequence:

- real hunks;
- Husk pseudo-hunks;
- expanded Lazy pseudo-hunks.

Selecting a Husk or Lazy pseudo-hunk:

- updates selection and counters;
- may scroll to that pseudo-hunk;
- does not fetch;
- does not enrich;
- does not alter FileSequence order.

Only activating a LazyFile plank asks ChangeSet to submit that file's explicit canonical request to the single request lane.

### 66.16 FileTree interaction

FileTree obtains Navigation through Context:

```ts
const navigation = useNavigation();
```

Clicking a file row:

1. unfolds the file when required;
2. resolves that file’s first participating hunk;
3. invokes ordinary navigation.

```ts
void navigation.navigate({
  kind: "hunk",
  hunk,
});
```

FileTree does not ask Navigation to navigate to a file.

Directories only change directory expansion.

Opening FileTree:

1. reads the selected FileCard from DOM;
2. finds the corresponding FileTree row;
3. applies `aria-current`;
4. reveals it inside the FileTree’s own scroll container.

That sidebar scroll does not move the main page.

### 66.17 FileCard interaction

FileCard obtains Navigation only where structural behavior requires it.

A structural owner may call:

```ts
navigation.selectHunk(replacement);
```

or:

```ts
navigation.clearHunkSelection();
```

before removing the selected target.

This applies to:

- selected HuskFile becoming FullFile;
- selected LazyFile becoming FullFile;
- folding a selected file;
- folding a directory containing selection;
- future notebook region replacement.

Representation-only rich/virtual or split/inline replacement does not call Navigation.
Code-line fold expansion and collapse also do not call Navigation because code folds contain no hunk targets and cannot remove selection.

### 66.18 FileCard-local representation operations

These remain FileCard-owned:

```ts
function projectSelectedHunk(
  fileCard: HTMLElement,
): void;
```

```ts
async function waitToEnrich(
  fileCard: HTMLElement,
): Promise<void>;
```

`projectSelectedHunk` restores decoration for an identity that remains on the FileCard.

It never:

- selects;
- clears;
- updates counters;
- updates FileTree;
- scrolls;
- fetches.

`waitToEnrich` changes only FullFile-local rendering and resolves after rich FileBody materialization and projection.

Navigation may request `waitToEnrich` for a particular FileCard through the approved scoped DOM event or callback capability.

Navigation does not receive the FullFile render-mode signal.

### 66.19 Line pins

Line-pin functionality belongs in `navigation.tsx` because it is the second authorized main-page viewport-moving system.

It remains internally separate from `NavigationCommand` and `navigate`.

```text
NavigationCommand
    → one-shot hunk navigation
    → may select a hunk

line-pin restoration
    → repeated viewport stabilization
    → never selects a hunk
```

Line-pin identity remains encoded in the URL.

The controller retains:

- current URL parsing and writing;
- pin highlighting;
- repeated restoration while files load and enrich;
- retry and stabilization timers;
- the ability to move the viewport repeatedly until stable.

Line-pin restoration never:

- calls `selectHunk`;
- changes hunk counters;
- changes FileTree highlighting;
- changes FileSequence order;
- creates a `NavigationCommand`.

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
  onToggleHelp: () => void;
};
```

```tsx
function HintHud(
  props: HintHudProps,
) {
  const navigation = useNavigation();

  return (
    <nav
      class="hunk-nav"
      aria-label="Hunk navigation"
    >
      <button
        type="button"
        onClick={() =>
          void navigation.navigate({
            kind: "next-hunk",
          })
        }
        title="Next hunk (n)"
      >
        Next <kbd>n</kbd>
      </button>

      <button
        type="button"
        onClick={() =>
          void navigation.navigate({
            kind: "previous-hunk",
          })
        }
        title="Previous hunk (N)"
      >
        Prev <kbd>N</kbd>
      </button>

      <button
        type="button"
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

They are not variants of one union and are not grouped under a HUD owner.

Debug sampling remains owned by `DebugHud` lifetime:

```tsx
<Show when={debugOpen()}>
  <DebugHud />
</Show>
```

Closed Debug performs no RAF sampling or DOM counting.

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

It receives concrete callbacks rather than grouped owner interfaces.

ChangeSet reload intentionally has no dedicated visible control. `R` is its only standing reload binding. An error-state `RetryButton` may still invoke reload as the explicit retry action.

```tsx
function Hotkeys(
  props: HotkeysProps,
) {
  const navigation = useNavigation();

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

        void navigation.navigate({
          kind: "next-hunk",
        });

        return;
      }

      if (
        event.code === "KeyN" &&
        event.shiftKey
      ) {
        event.preventDefault();

        void navigation.navigate({
          kind: "previous-hunk",
        });

        return;
      }

      if (event.code === "KeyP") {
        event.preventDefault();

        void navigation.navigate({
          kind: "top",
        });

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

Buttons call their actual owner directly:

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

There is no central bus between the user interaction and the actual owner.

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

        <FileTree />

        <FileCards />

        <div class="hud-stack">
          <Show when={debugOpen()}>
            <DebugHud />
          </Show>

          <HintHud
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

1. Every active ChangeSet has exactly one Navigation controller.
2. NavigationController state is ephemeral and non-reactive.
3. Selected-hunk identity remains in FileCard DOM.
4. Participating navigation order remains current DOM order.
5. Line-pin identity remains in the URL.
6. `NavigationProvider` exposes operations but no controller state.
7. `useNavigation()` never constructs another controller.
8. `navigate` is the only ordinary hunk-navigation viewport mover.
9. Line-pin restoration is the only second authorized main-page viewport mover.
10. Line-pin restoration never selects a hunk.
11. `selectHunk` and `clearHunkSelection` never scroll.
12. Rich/virtual replacement never invokes Navigation.
13. `projectSelectedHunk` never selects, scrolls, enriches, or updates counters.
14. `waitToEnrich` remains FileCard-owned.
15. Navigation re-resolves a target after `waitToEnrich`.
16. FileTree navigation resolves a hunk before invoking Navigation.
17. There are no file or directory navigation commands.
18. Selecting a pseudo-hunk never starts a backend request.
19. Navigation never changes FileSequence order.
20. Exactly one application hotkey listener is mounted.
21. Inactive ChangeSets have no hotkey listener.
22. Hotkeys contain no generic command or dispatch abstraction.
23. Hotkeys ignored inside editable controls preserve native behavior.
24. Recognized hotkeys call `preventDefault()` before invoking their operation.
25. Help and Debug remain independent state values.
26. Closed Debug performs no sampling.
27. Provider cleanup removes every listener, frame, timer, retry and observer.
28. A disposed controller performs no later DOM mutation or scrolling.
29. Browser text-side selection remains independent from Navigation.
30. `NavigationCommand` is the only surviving application command type.
31. `HintHud` and `DebugHud` definitions remain adjacent in source.
32. `HintHud` and `DebugHud` remain adjacent inside the rendered HUD stack.
33. `HelpModal` remains outside the HUD stack and never separates `HintHud` from `DebugHud`.
34. `ChangeSetTitle` contains no Show All or Fold All controls, and Help contains no corresponding rows.
35. `s` and `f` are not application hotkeys.

This is the complete corrected navigation-and-hotkeys plan for approval.

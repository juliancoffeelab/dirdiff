# Frontend rewrite plan

## 1. Setup

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

The rewritten frontend is built beside the existing frontend. Existing files under `frontend/src/` remain `v_old`, while the rewrite is `v_new` and lives under `frontend/src/new/` until final cutover.

This is not a gradual movement of `v_old` application functions into new files. `v_new` implements the architecture specified by the topic files in `../spec/` as an independent frontend.

Visual parity, intermediate-stage scope and every authorized exception are governed by [guidance.md](guidance.md). This setup chapter defines no additional visual policy.

`frontend/src/main.tsx` remains the sole Vite entrypoint. It attaches Solid to the root DOM element and temporarily selects which complete frontend to mount. It stores no workspace or domain state.

The visible applications are:

```text
v_old → frontend/src/App.tsx
v_new → frontend/src/new/hud/App.tsx
```

There is no second entrypoint, `new/main.tsx`, or `new/root.tsx`.

The transitional source tree is:

```text
frontend/src/
├── App.tsx
├── Header.tsx
├── ...
├── app/
│   └── ...
├── main.tsx
└── new/
    ├── api/
    │   ├── api.ts
    │   └── queryClient.tsx
    ├── comp/
    │   ├── AutocompleteInput.tsx
    │   ├── Select.tsx
    │   └── Toasts.tsx
    ├── hud/
    │   ├── App.tsx
    │   ├── AppHeader.tsx
    │   ├── Tabs.tsx
    │   ├── Profile.tsx
    │   ├── ChangeSet.tsx
    │   ├── FileCard.tsx
    │   ├── navigation.tsx
    │   ├── DiffGrid.tsx
    │   ├── NotebookFile.tsx
    │   └── folds.ts
    ├── styles.css
    └── utils.ts
```

The migration selector chooses a complete provider tree and App, not only an App component.

`v_old` remains:

```text
current QueryClientProvider
└── current ToastProvider
    ├── current root ErrorBoundary
    │   └── current App
    └── current ToastViewport
```

`v_new` uses the rewritten composition from the specification:

```text
new ToastProvider
├── new QueryProvider
│   └── new root ErrorBoundary
│       └── new/hud/App
└── new ToastViewport
```

The two versions share only the browser document, root mount element, Python backend contract, and temporary frontend-switch operation.

They do not share QueryClient instances, query caches, Toast state, ErrorBoundaries, Solid state, Context, DOM references, event listeners, timers, observers, or application CSS.

The temporary version type is:

```ts
type FrontendVersion = "v_old" | "v_new";
```

The selected implementation is stored independently from workspace state:

```text
localStorage["dirdiff:frontend-version"] = "v_old" | "v_new"
```

`v_old` is the initial default.

The version does not live in the URL because `v_old` currently reconstructs the query string and may discard unrelated parameters. The storage value selects an implementation only; the active frontend still reconstructs its entire workspace from the current URL.

The dirdiff brand in the top-left becomes a button in both versions.

The `v_old` button retains the current visual treatment and switches to `v_new`. The `v_new` button has the same `dirdiff` text, geometry, typography and placement, but uses the green treatment authorized by Appendix A and switches back to `v_old`.

The buttons communicate their destination through their title and accessible label:

```text
v_old button → Switch to v_new
v_new button → Switch to v_old
```

Pressing either button:

1. stores the destination version;
2. leaves the current pathname, query, and hash unchanged;
3. reloads the page;
4. causes `main.tsx` to mount only the selected frontend.

Switching is an intentional complete reset boundary. No Solid state, query state, DOM state, selected hunk, input state, or pending orchestration crosses between versions. The newly mounted frontend starts from the current URL.

The hard reload also aborts outstanding requests, disposes the previous reactive graph and global listeners, clears in-memory caches, and prevents both versions’ stylesheets from remaining active.

`main.tsx` must conditionally load only the selected branch. It must not statically import both Apps and both stylesheets into the page.

The `v_old` branch dynamically loads the existing App, query client, Toast implementation, and stylesheet. The `v_new` branch dynamically loads the equivalent modules under `new/`.

Code under `frontend/src/new/` must not import application code from `v_old`.

Prohibited examples include:

```ts
import { Header } from "../Header";
import { queryClient } from "../queryClient";
import { useToasts } from "../Toasts";
import "../styles.css";
```

`v_new` may import only:

- other modules under `new/`;
- third-party packages;
- browser APIs.

Explicitly preserved renderer files such as `DiffGrid.tsx` and `folds.ts` receive local copies under `new/hud/`. `v_new` does not import their `v_old` copies.

The backend remains one contract. There are no `/api/v_old` or `/api/v_new` endpoints, compatibility responses, or version-dependent backend behavior. If the backend contract changes during migration, both frontends are updated together.

Setup is implemented in this order:

1. Create the initial `frontend/src/new/` structure.
2. Create the minimal `v_new` Toast, QueryProvider, AppHeader, App, and stylesheet modules needed to mount an independent `v_new` root.
3. Turn the existing `v_old` dirdiff brand into the switch button.
4. Add the green `v_new` brand with the inverse action.
5. Change `main.tsx` into the temporary version selector.
6. Make both branches conditionally load their own modules and CSS.
7. Verify switching and isolation before adding domain functionality.

The initial `v_new` App may contain only its AppHeader and an explicit message that the rewrite is under construction. It must already use the `v_new` providers and stylesheet. It must not render `v_old` UI as a placeholder.

Setup is complete when:

- `main.tsx` remains the only Vite entrypoint;
- `v_old` is the initial default;
- both brand buttons switch to the opposite version;
- F5 preserves the selected version;
- switching preserves the exact pathname, query, and hash;
- the newly mounted frontend reconstructs state from the URL;
- only one App and one provider tree are mounted;
- only the active version’s stylesheet is loaded;
- `v_new` imports no `v_old` application modules;
- `v_old` behavior outside the switch remains unchanged;
- `make format` and `make tscheck` pass;
- both versions work through the normal Vite-backed dirdiff session.

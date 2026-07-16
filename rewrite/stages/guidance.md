# Rewrite implementation guidance

This file governs implementation of every practical chapter in this directory. The topic files under `../spec/` define frontend behavior and architecture; the chapter files define implementation order. The two `full.md` files are frozen original references rather than maintained authorities.

Items in [followups.md](followups.md) are explicit post-rewrite work, not deferred implementation inside a practical chapter. Do not pull a TODO or FIXME from that file into Chapters 1–6 without a separate user decision.

`AGENTS.md` continues to apply except where this guidance explicitly narrows a rule for the specification-driven frontend rewrite.

## Visual contract

At the same viewport, URL, backend data and UI state, every surface and behavior implemented by the current practical chapter must be a pixel-perfect 1:1 copy of its `v_old` counterpart. Architectural improvement never authorizes visual redesign, approximation, cleanup or a merely similar result.

Functionality explicitly assigned to a later practical chapter is not implemented yet and is outside the current chapter's parity comparison. An intermediate chapter may therefore omit later surfaces entirely or render only the placeholder boundary that its chapter explicitly requires. This is not a visual exception for implemented functionality. Once the final implementation chapter is complete, the complete application must satisfy pixel-perfect parity except for the six authorized differences below.

Only the following visual differences are authorized:

1. Show All and Fold All are removed.

   Their ChangeSet title controls and corresponding Help rows do not exist in `v_new`. No replacement controls are added.

2. The `v_new` dirdiff plank is green.

   It retains the same `dirdiff` text, dimensions, typography, spacing, border, placement and interaction footprint as `v_old`. Only its color treatment changes to make the active `v_new` implementation immediately visible.

3. File-loading status is more compact.

   Status shown while files are being loaded may use the compact AppHeader presentation specified in `../spec/03_file_presentation.md`. This exception applies only to file-loading progress, failure and long-running-file status. It does not authorize unrelated Header, status, summary or layout changes.

4. Three Tab-local metadata refresh buttons are added.

   Refs receives a refs refresh button at the top-right of its open autocomplete suggestion panel, and Branch Review places its branches-and-remotes refresh button in the same location. Preset places its preset-catalog refresh button beside the preset-kind tabs. Each icon rotates and is disabled while fetching, becomes enabled error red after failure, and returns to its ordinary treatment only after success. This exception authorizes those three controls only. There is no visible ChangeSet reload button.

5. Compact shell errors can open complete details in a top-layer popover.

   Repository, refs/defaults, preset and profile failures preserve their compact `v_old` layout footprint. The compact red error presentation is keyboard and click accessible and opens an ErrorPopover containing the complete message, initially open stack when available, and RetryButton. The popover consumes no document layout space. In the failed state, a metadata refresh icon opens this popover and retry occurs inside it.

6. Manifest entries appear immediately during sequential file loading.

   Once a manifest is available, `v_new` renders its complete FileTree and one stable FileCard per manifest entry instead of waiting for each file result before inserting that entry. Ordinary queued or fetching files use their state-specific HuskFile and HuskFileHeader until they become FullFile or LazyFile. This exception applies only to the in-progress file-loading presentation; loaded FileTree entries, FullFile rendering, dimensions, typography, colors, sticky behavior and final layout remain subject to pixel-perfect parity.

No other visual difference is permitted. Visual behavior must be reviewed in the running browser with screenshots at matching state; DOM structure or computed measurements alone are not visual verification.

This rewrite is not a mobile application. Pixel-perfect parity is required at supported desktop viewports. Mobile and narrow responsive layouts are out of scope and must not drive implementation work.

## Specification-driven module boundaries

The `AGENTS.md` 1000-line module rule governs free-form module creation. It does not override a file or component boundary explicitly required by the frontend specification.

When the specification names a module, create and retain that module even when its current implementation is short. Do not merge specified modules, invent a larger owner or introduce a workaround merely to satisfy the free-form line-count heuristic.

Outside explicit specification boundaries, the original `AGENTS.md` module rule applies.

## Documentation inside `new/`

Before adding a function shorter than five lines of code, inspect every use. A
single-use implementation detail should normally be inlined at that use with a
plain inline comment explaining the operation. A short function used multiple
times only within one owning function should be nested inside that owner. A
separate short function remains appropriate only when it is a genuine reusable
interface or an explicit domain operation whose name is part of the design.

Every JavaScript, TypeScript and TSX module under `frontend/src/new/` must begin with a thorough module JSDoc that explains:

- its public interface;
- why the module exists;
- what it owns and guarantees;
- what it must not own or do.

Every function under `frontend/src/new/` must have JSDoc explaining its purpose and caller contract. Public functions must state what callers provide, what callers may expect, and the constraints callers must respect. A title-only or restatement-of-the-name comment is not sufficient.

Every named type alias, interface, class and enum under `frontend/src/new/` must have JSDoc explaining the contract it represents, the meaning and requirements of its fields or variants, and what it must not represent. Public types must state what callers may provide and rely on. Declaration-merging interfaces must document both the shape they register and whether they change field presence or only field contents.

Docblocks document the code's actual interface and enduring ownership contract. They must not discuss migration versions, implementation stages, temporary status, future work, historical context, or justify why an incomplete implementation is acceptable. Those are implementation notes, not API documentation.

When temporary sequencing context is genuinely necessary, place a plain inline comment beside the relevant statement inside the function body. Keep it concrete and local; do not turn module or function JSDoc into a migration explanation.

Stylesheets and other formats without JSDoc syntax must carry an equivalent module-level comment.

This documentation requirement does not authorize retrofitting `v_old`. Existing frontend files remain old code. The transitional `main.tsx` may contain a short ordinary comment explaining the temporary branch-selection responsibility.

## Required implementation discipline

- Assert required data at its boundary. Do not make required inputs optional to avoid handling an invariant.
- Do not create compatibility shims. When an interface changes, update every in-scope caller.
- The temporary frontend toggle in `frontend/src/main.tsx` is the sole authorized
  transition exception: immediately before reload it translates old browser
  `project_id` into new `repo_id` or `preset_type`, and performs the inverse when
  switching back. Neither frontend tree may accept the other tree's URL vocabulary.
- Do not import `v_old` application modules into `v_new`.
- Implement only the current practical chapter. Do not pull later-stage behavior forward merely because its eventual location is already known.
- In project-owned terminology, `request` means an HTTP request, ordinary use as
  a verb, or `pull_request`. No other project-owned type, variable, state, lane,
  callback, command, or concept may be named `request`. External APIs such as
  Solid's `requestCallback` and the browser's `requestAnimationFrame` retain
  their required names.
- Do not change test behavior without explicit user approval, and do not add test-only helpers.
- Use the existing hot-reloadable dirdiff/Vite session for browser verification. Do not start an alternative server.
- Run `make format` and `make tscheck` after changes.
- Verify user-visible rendering in the browser with actual screenshots.
- Do not rebuild or commit generated frontend bundles unless explicitly requested.

## Contradictions and unclear requirements

If the specification contradicts itself, the current stage, `AGENTS.md`, the codebase, or a language/tooling requirement, stop before implementing a resolution.

Report the problem clearly and concisely:

1. quote or identify the two conflicting requirements;
2. explain the concrete implementation consequence;
3. provide the smallest reasonable choices, when choices exist.

Wait for the user to correct the specification or choose the intended requirement. Do not silently prioritize one side, edit the specification without permission, change a project-wide setting, or introduce an unusual workaround to preserve both sides.

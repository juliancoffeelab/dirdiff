## Appendix A. Permitted visual differences

Only the following visual differences between `v_old` and `v_new` are authorized:

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

7. File expansion and line-fold controls use the canonical terminology.

   File and directory expansion controls use “Collapse” and “Expand” in titles and accessible names. The aggressive-fold preference uses “Fold” for unchanged lines. This exception changes those strings only; it does not authorize different geometry, styling, placement, or behavior.

No other visual difference is permitted. Everything not listed above must remain a pixel-perfect 1:1 copy of `v_old`.

## Appendix B. Explicitly forbidden selection rectangles

Hunk selection must not draw a rectangular outline around the complete FileCard or around a LazyFile explicit-load plank. Selected real or virtual hunk-row decoration remains governed by the hunk specification. This prohibition is tracked separately from the added or changed surfaces in Appendix A and makes no claim that such rectangles are part of the stable visual contract.

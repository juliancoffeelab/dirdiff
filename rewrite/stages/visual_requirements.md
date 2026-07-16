## Appendix A. Permitted visual differences

Only the following visual differences between `v_old` and `v_new` are authorized:

1. Show All and Fold All are removed.

   Their ChangeSet title controls and corresponding Help rows do not exist in `v_new`. No replacement controls are added.

2. The `v_new` dirdiff plank is green.

   It retains the same `dirdiff` text, dimensions, typography, spacing, border, placement and interaction footprint as `v_old`. Only its color treatment changes to make the active `v_new` implementation immediately visible.

3. File-loading status is more compact.

   Status shown while files are being loaded may use the compact AppHeader presentation specified in `../spec/03_file_presentation.md`. This exception applies only to file-loading progress, failure and long-running-file status. It does not authorize unrelated Header, status, summary or layout changes.

4. Three Tab-local metadata refresh buttons are added.

   Refs receives a refs refresh button, Branch Review receives a branches-and-remotes refresh button, and Preset receives a preset-catalog refresh button. This exception authorizes those three controls only. Their exact placement, dimensions and appearance remain to be approved when the Tabs UI is implemented. There is no visible ChangeSet reload button.

No other visual difference is permitted. Everything not listed above must remain a pixel-perfect 1:1 copy of `v_old`.

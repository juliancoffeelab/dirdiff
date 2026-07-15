## Appendix A. Permitted visual differences

Only the following visual differences between `v_old` and `v_new` are authorized:

1. Show All and Fold All are removed.

   Their ChangeSet title controls and corresponding Help rows do not exist in `v_new`. No replacement controls are added.

2. The `v_new` dirdiff plank is green.

   It retains the same `dirdiff` text, dimensions, typography, spacing, border, placement and interaction footprint as `v_old`. Only its color treatment changes to make the active `v_new` implementation immediately visible.

3. File-loading status is more compact.

   Status shown while files are being loaded may use the compact AppHeader presentation specified in `../spec/03_file_presentation.md`. This exception applies only to file-loading progress, failure and long-running-file status. It does not authorize unrelated Header, status, summary or layout changes.

No other visual difference is permitted. Everything not listed above must remain a pixel-perfect 1:1 copy of `v_old`.

## 6. Direct hotkeys and Help

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to the hotkey and Help requirements in `../spec/06_components_and_modules.md` and `../spec/07_navigation_and_hotkeys.md`. This chapter deliberately implements only the operations that do not require hunk tokens, hunk selection or NavigationProvider.

Every visible result must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. HelpModal remains visually identical to the existing implementation.

Implemented in this order:

1. One active listener

   Add the single private hotkey lifecycle component in `ChangeSet.tsx`. It exists only while the active ChangeSet content exists, installs one `keydown` listener, removes that listener on cleanup, contains no generic command or dispatch abstraction, and receives concrete callbacks from the owners of each operation.

2. Input protection

   Preserve native keyboard behavior when the event is already prevented, uses Meta, Control or Alt, or originates from an input, textarea, select or content-editable element. Shift is not rejected globally. Call `preventDefault()` only after recognizing one of this chapter's supported keys.

3. Direct bindings

   Implement exactly these bindings:

   | Key | Operation |
   |---|---|
   | `p` | scroll the main page directly to the top |
   | `t` | toggle the active ChangeSet FileTree |
   | `i` | toggle the workspace inline/split view |
   | `r` | reload the active ChangeSet |
   | `h` | toggle HelpModal |

   `p` uses the direct browser scroll operation in this chapter because Navigation does not exist yet. Chapter 7 routes the same binding through the completed Navigation controller without changing its key or mounting another listener.

   Reload retains no standing visible button. The error-state RetryButton remains a separate explicit user action.

4. Help state and overlay

   Add independent ChangeSet-owned Help visibility and the private `HelpModal` in `ChangeSet.tsx`. HelpModal remains an overlay under `hud/`; it is not a HUD-stack component and this chapter does not introduce HintHud merely to open it.

   Preserve the final Help row order. The removed `s` and `f` rows do not exist. The `n`, `N` and `d` rows remain visible but are disabled and gray until Chapter 7 implements those bindings. The working `p`, `t`, `i`, `r` and `h` rows retain their ordinary presentation.

Explicitly absent until Chapter 7:

- `navigation.tsx`, NavigationProvider and useNavigation;
- DOM hunk tokens, selected hunk and counters;
- Next and Previous;
- `n` and `N` bindings;
- DebugHud and the `d` binding;
- HintHud;
- FileTree selected-hunk calculation and display;
- scroll-follow and `waitToEnrich` routing;
- line-pin restoration;
- navigation-specific browser listeners or observers.

At the end of Chapter 6, the active ChangeSet supports Go to top, FileTree toggle, inline/split toggle, reload and Help through one direct hotkey listener. It still has no hunk navigation or selection subsystem.

Chapter 7 extends this same listener with navigation-specific bindings and connects the remaining DOM navigation design. It must not duplicate or replace the working non-navigation hotkeys from this chapter.

`v_old` remains available until final cutover is explicitly authorized.

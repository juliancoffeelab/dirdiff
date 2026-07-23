## Post-7. FileTree presentation

Read [guidance.md](guidance.md) before starting this chapter. Implement this chapter according to the FileTree contract in Sections 25.5–25.13 of [03_file_presentation.md](../spec/03_file_presentation.md) and the selection-display contract in [08_hunk_navigation.md](../spec/08_hunk_navigation.md).

This chapter corrects FileTree presentation after explicit hunk navigation exists. It does not implement any part of Chapters 8–10.

1. Make every directory row, file row, progressive statistic, and expansion marker read current reactive ChangeSet inputs rather than values captured by the recursive renderer.

2. Keep ChangeSet as the only file-expansion authority and remove independently stored directory expansion. Calculate every directory bottom-up as expanded when any descendant file is reachable. Explicit file expansion wins; unresolved Husks remain reachable so sequential loading does not collapse the directory hierarchy, and LazyFiles remain reachable while their plank is visible unless explicitly collapsed.

3. Make each directory square the sole directory-expansion control. It bulk-writes the requested expansion to every descendant file, after which the reachability calculation determines that directory and its ancestors. Make each FullFile square the sole individual expansion control in both FileTree and FileCard. Both FullFile squares call the same ChangeSet action. Husk and Lazy squares remain inert and empty.

4. Keep directory and file labels separate from their squares. Every visible directory label ends with `/`; the slash is presentation and does not change manifest identity. FileTree labels remain inert until Chapter 8 defines their navigation. The main FileHeader path, counters, statistics, and remaining content are inert selectable content. No label toggles expansion, and no square selects, repairs, navigates, loads, or scrolls either viewport.

5. Display LazyFile rows as collapsed in FileTree through deferred, fetching, and localized-error states. Direct plank activation does not change expansion. A successful explicit fetch replaces LazyFile with FullFile before expanding that FullFile; a failed fetch remains a collapsed error-flavoured LazyFile. A resulting FullFile retains the color of its non-error Lazy reason.

6. Display FileCard-local virtual mode as `V` through one FileTree-local calculation. While FileTree is open, scan stable FileCard DOM and observe only `data-file-render` mutations. Disconnect and discard that presentation-only map when FileTree closes. Navigation, virtualization, and ChangeSet state must not read it.

7. Render selected-file highlighting declaratively from `HunkDisplay.selectedFileIndex`. Collapsing a directory or file changes only shared expansion and `.skip` participation; it never changes selected identity. A selected row beneath a collapsed directory is legitimately absent and reappears highlighted when the directory becomes reachable again.

8. Follow selected-file changes only inside `.file-tree-groups`. Never call `scrollIntoView()`, expand a directory, queue a microtask, retry in an animation frame, or move the main page. Hunk changes within the already highlighted file do not repeat the sidebar scroll.

9. Preserve the established FileTree dimensions, typography, colors, statistics, sticky geometry, and markers except for the explicitly corrected `V` and Lazy-state presentation.

Verify in the browser that directory reachability follows current descendant file expansion; collapsing the final reachable file collapses the required ancestor chain while another reachable descendant keeps its ancestors open; directory squares bulk-toggle descendants; FileTree and FileCard FullFile squares update the same state; labels never toggle expansion; main FileHeader text is selectable; rich/virtual transitions update filled/`V` markers; selected-file changes highlight and privately reveal the correct row without repeating the scroll for hunk changes in one file; unreachable ancestors hide the row without changing selection; FileTree labels remain non-navigating; FileTree interaction never moves the main page; Lazy rows stay collapsed through fetch and error, successful FullFile replacement then expands, and a non-error Lazy reason keeps its color.

Chapter 8 implements the separately approved FileTree label navigation. Chapter 9 implements the separately approved scroll-follow design. Chapter 10 implements the separately approved line-pin design.

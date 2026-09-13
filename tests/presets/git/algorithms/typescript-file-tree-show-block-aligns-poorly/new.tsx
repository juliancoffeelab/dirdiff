function FileTreeGroups() {
  return (
    <>
      <Show
        when={groups().length > 0}
        fallback={<p class="file-tree-empty">No files loaded yet.</p>}
      >
        <For each={groups()}>
          {(group) => (
            <section class="file-tree-group">
              <div class="file-tree-directory">
                <button
                  type="button"
                  class="file-tree-visibility-toggle"
                  onClick={() =>
                    setDirectoryExpanded(
                      group,
                      !directoryExpanded(group),
                    )
                  }
                  aria-label={
                    directoryExpanded(group)
                      ? `Fold ${group.label}`
                      : `Show ${group.label}`
                  }
                >
                  <VisibilityIndicator
                    size="small"
                    visible={directoryExpanded(group)}
                  />
                </button>
                <button
                  type="button"
                  class="file-tree-directory-target"
                  onClick={() => props.onScrollToDirectory(group)}
                >
                  {group.label}
                </button>
                <TreeLineStats stats={groupLineStats(group)} />
              </div>
              <For each={group.files}>
                {(file) => {
                  const virtualized = () => fileIsVirtualized(file);
                  const lazyReason = () => fileTreeLazyReason(file);
                  return (
                    <div
                      class="file-tree-file"
                      data-file-tree-file-id={fileElementId(
                        fileKey(file),
                      )}
                      classList={{
                        added:
                          fileKindStatus(file.file_kind) === "added",
                        removed:
                          fileKindStatus(file.file_kind) === "deleted",
                        renamed:
                          fileKindStatus(file.file_kind) === "renamed",
                        untracked:
                          fileKindStatus(file.file_kind) ===
                          "untracked",
                        lazy: lazyReason() !== null,
                        "lazy-error": lazyIsError(file.lazy),
                        "lazy-generated": lazyReason() === "generated",
                        "lazy-too-big": lazyReason() === "too_big",
                        "active-hunk-file": fileIsActiveHunkFile(file),
                      }}
                      aria-current={
                        fileIsActiveHunkFile(file) ? "true" : undefined
                      }
                      title={fileDisplayName(file)}
                    >
                      <button
                        type="button"
                        class="file-tree-visibility-toggle"
                        onClick={() =>
                          setFileExpanded(file, !fileExpanded(file))
                        }
                        aria-label={
                          fileExpanded(file)
                            ? `Fold ${fileDisplayName(file)}`
                            : `Show ${fileDisplayName(file)}`
                        }
                      >
                        <VisibilityIndicator
                          size="small"
                          visible={fileExpanded(file)}
                          virtualized={virtualized()}
                        />
                      </button>
                      <button
                        type="button"
                        class="file-tree-file-target"
                        aria-current={
                          fileIsActiveHunkFile(file)
                            ? "true"
                            : undefined
                        }
                        onClick={() => props.onScrollToFile(file)}
                      >
                        <span class="file-tree-file-name">
                          {fileDisplayName(file)}
                        </span>
                        <TreeLineStats stats={fileLineStats(file)} />
                      </button>
                    </div>
                  );
                }}
              </For>
            </section>
          )}
        </For>
      </Show>
    </>
  );
}

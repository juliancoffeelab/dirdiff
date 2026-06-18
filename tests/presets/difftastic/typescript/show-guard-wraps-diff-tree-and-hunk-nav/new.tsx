export function AppView() {
  return (
    <>
      <p class={`status ${diff.status()}`}>{diff.statusText()}</p>
      <Show when={preferences() !== null}>
        <GracefulErrorBoundary title="Could not render diff">
          <FileList
            files={ui.displayFiles()}
            loadedDiff={ui.loadedDiff()}
            currentParamsIdentity={diff.currentParamsIdentity}
            directoryExpansion={ui.directoryExpansion()}
            fileExpansion={ui.fileExpansion()}
            loadingFiles={diff.loadingFiles()}
            fileErrors={diff.fileErrors()}
            linePin={navigation.linePin()}
            forcedRichFileIds={ui.forcedRichFileIds()}
            aggressiveFolds={preferences()!.aggressive_folds}
            onFileVirtualizedChange={ui.setFileVirtualized}
            diffViewMode={diffViewMode()}
            setDirectoryExpansion={ui.setDirectoryExpansion}
            setFileExpansion={ui.setFileExpansion}
            setLoadingFiles={diff.setLoadingFiles}
            setFileErrors={diff.setFileErrors}
            updateLoadedDiff={ui.updateLoadedDiff}
            onSetAllExpanded={ui.setAllFilesExpanded}
          />
        </GracefulErrorBoundary>
        <GracefulErrorBoundary title="Could not render file tree">
          <FileTreeSidebar
            files={ui.displayFiles()}
            directoryExpansion={ui.directoryExpansion()}
            fileExpansion={ui.fileExpansion()}
            activeHunkFileId={ui.activeHunkFileId()}
            virtualizedFileIds={ui.virtualizedFileIds()}
            open={navigation.fileTreeOpen()}
            onOpenChange={navigation.setFileTreeOpen}
            setDirectoryExpansion={ui.setDirectoryExpansion}
            setFileExpansion={ui.setFileExpansion}
            onScrollToDirectory={navigation.scrollToDirectory}
            onScrollToFile={navigation.scrollToFile}
          />
        </GracefulErrorBoundary>
        <HunkNav
          debugOpen={navigation.debugMenuOpen()}
          helpOpen={navigation.helpOpen()}
          hunkPosition={navigation.hunkPosition()}
          onHelpOpenChange={navigation.setHelpOpen}
          onNext={navigation.scrollNext}
          onPrev={navigation.scrollPrev}
        />
      </Show>
    </>
  );
}

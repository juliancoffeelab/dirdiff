function FileTreeDirectory(props: {
  group: FileGroup;
  directoryExpanded: (group: FileGroup) => boolean;
  setDirectoryExpanded: (group: FileGroup, expanded: boolean) => void;
}) {
  return (
    <div class="file-tree-groups">
      <For each={groups()}>
        {(group) => (
          <section class="file-tree-group">
            <div class="file-tree-directory">
              <button
                type="button"
                class="file-tree-visibility-toggle"
                onClick={() =>
                  props.setDirectoryExpanded(props.group, !props.directoryExpanded(props.group))
                }
                aria-label={
                  props.directoryExpanded(props.group)
                    ? `Fold ${props.group.label}`
                    : `Show ${props.group.label}`
                }
              />
            </div>
          </section>
        )}
      </For>
    </div>
  );
}

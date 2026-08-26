function FileTreeDirectory(props: {
  group: FileGroup;
  directoryExpanded: (group: FileGroup) => boolean;
  setDirectoryExpanded: (group: FileGroup, expanded: boolean) => void;
}) {
  return (
    <button
      type="button"
      class="file-tree-visibility-toggle"
      onClick={() =>
        props.setDirectoryExpanded(
          props.group,
          !props.directoryExpanded(props.group),
        )
      }
    >
      <VisibilityIndicator visible={props.directoryExpanded(props.group)} />
    </button>
  );
}

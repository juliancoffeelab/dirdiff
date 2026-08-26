type FileEntry = { render_kind: string };

declare function Show(props: { when: boolean; fallback?: unknown; children: unknown }): unknown;
declare function FilePlaceholder(props: { file: FileEntry }): unknown;
declare function PlainSplitFileDiff(props: { file: FileEntry }): unknown;
declare function DiffGrid(props: { file: FileEntry }): unknown;

export function FileCard(props: { file: FileEntry }) {
  const canRenderRows = () => true;
  const shouldRenderRichBody = () => true;

  return (
    <Show when={props.file.render_kind !== "notebook"}>
      <Show
        when={canRenderRows()}
        fallback={<FilePlaceholder file={props.file} />}
      >
        <Show
          when={shouldRenderRichBody()}
          fallback={<PlainSplitFileDiff file={props.file} />}
        >
          <DiffGrid file={props.file} />
        </Show>
      </Show>
    </Show>
  );
}

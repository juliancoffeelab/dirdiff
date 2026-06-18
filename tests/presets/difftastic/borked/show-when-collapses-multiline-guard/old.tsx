import { Show } from "solid-js";

type Repo = {
  selectedRepoId(): number | null;
  presetCatalog(): object | null;
};

declare const repo: Repo;
declare const controls: () => object | null;

export function Example() {
  return (
    <Show
      when={
        repo.selectedRepoId() !== null &&
        controls() !== null &&
        repo.presetCatalog() !== null
      }
    >
      <>ok</>
    </Show>
  );
}

import { For, Show } from "solid-js";
import type { RepoMark } from "./api";

export function RepoPicker(props: {
  repos: RepoMark[];
  error: string;
  onSelect: (repo: RepoMark) => void;
}) {
  return (
    <section class="repo-picker" aria-label="Marked repositories">
      <div class="repo-picker-heading">
        <h2>Choose a repo</h2>
        <p>Select a marked repository before loading repo-backed diffs.</p>
      </div>
      <Show when={props.error !== ""}>
        <p class="repo-picker-error">{props.error}</p>
      </Show>
      <div class="repo-list">
        <For each={props.repos}>
          {(repo) => (
            <button
              type="button"
              class="repo-option"
              onClick={() => props.onSelect(repo)}
            >
              <span class="repo-option-name">{repo.name}</span>
              <span class="repo-option-path">{repo.path}</span>
            </button>
          )}
        </For>
      </div>
    </section>
  );
}

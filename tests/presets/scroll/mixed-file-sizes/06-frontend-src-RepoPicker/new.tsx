import { For, Show, createSignal } from "solid-js";
import { Trash2 } from "lucide-solid";
import type { RepoMark } from "./api";
import type { RepoListStatus } from "./fileUtils";

export function RepoPicker(props: {
  repos: RepoListStatus;
  error: string;
  onSelect: (repo: RepoMark) => void;
  onRemove: (repo: RepoMark) => void | Promise<void>;
}) {
  const [removingProjectId, setRemovingProjectId] = createSignal<number | null>(
    null,
  );
  const removeRepo = async (repo: RepoMark) => {
    if (!confirm(`Remove ${repo.name} from marked repositories?`)) {
      return;
    }
    setRemovingProjectId(repo.id);
    try {
      await props.onRemove(repo);
    } finally {
      setRemovingProjectId(null);
    }
  };

  return (
    <section class="repo-picker" aria-label="Marked repositories">
      <div class="repo-picker-heading">
        <h2>Choose a repo</h2>
        <p>Select a marked repository before loading repo-backed diffs.</p>
      </div>
      <Show when={props.error !== ""}>
        <p class="repo-picker-error">{props.error}</p>
      </Show>
      <Show
        when={props.repos.state === "loaded" ? props.repos.repos : null}
        fallback={<p class="repo-picker-loading">Loading marked repos...</p>}
      >
        {(repos) => (
          <div class="repo-list">
            <For each={repos()}>
              {(repo) => (
                <div class="repo-option-row">
                  <button
                    type="button"
                    class="repo-option"
                    onClick={() => props.onSelect(repo)}
                  >
                    <span class="repo-option-name">{repo.name}</span>
                    <span class="repo-option-path">{repo.path}</span>
                  </button>
                  <button
                    type="button"
                    class="repo-remove-button"
                    title={`Remove ${repo.name}`}
                    aria-label={`Remove ${repo.name}`}
                    disabled={removingProjectId() === repo.id}
                    onClick={() => void removeRepo(repo)}
                  >
                    <Trash2 class="repo-remove-icon" aria-hidden="true" />
                  </button>
                </div>
              )}
            </For>
          </div>
        )}
      </Show>
    </section>
  );
}

import { For, Show, createSignal } from "solid-js";
import { Trash2 } from "lucide-solid";
import type { RepoMark } from "./api";

export function RepoPicker(props: {
  repos: RepoMark[];
  error: string;
  onSelect: (repo: RepoMark) => void;
  onRemove: (repo: RepoMark) => void | Promise<void>;
  onPullRequest: (url: string) => void | Promise<void>;
}) {
  const [pullRequestUrl, setPullRequestUrl] = createSignal("");
  const [removingRepoId, setRemovingRepoId] = createSignal<number | null>(null);
  const submitPullRequest = (event: SubmitEvent) => {
    event.preventDefault();
    void props.onPullRequest(pullRequestUrl());
  };
  const removeRepo = async (repo: RepoMark) => {
    if (!confirm(`Remove ${repo.name} from marked repositories?`)) {
      return;
    }
    setRemovingRepoId(repo.id);
    try {
      await props.onRemove(repo);
    } finally {
      setRemovingRepoId(null);
    }
  };

  return (
    <section class="repo-picker" aria-label="Marked repositories">
      <div class="repo-picker-heading">
        <h2>Choose a repo</h2>
        <p>Select a marked repository before loading repo-backed diffs.</p>
      </div>
      <form class="repo-picker-pr" onSubmit={submitPullRequest}>
        <label class="field pull-request-field">
          <span>Pull request</span>
          <input
            value={pullRequestUrl()}
            placeholder="GitHub PR or GitLab MR URL"
            spellcheck={false}
            autocomplete="off"
            onInput={(event) => setPullRequestUrl(event.currentTarget.value)}
          />
        </label>
        <button class="load-button" type="submit">
          Load PR
        </button>
      </form>
      <Show when={props.error !== ""}>
        <p class="repo-picker-error">{props.error}</p>
      </Show>
      <div class="repo-list">
        <For each={props.repos}>
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
                disabled={removingRepoId() === repo.id}
                onClick={() => void removeRepo(repo)}
              >
                <Trash2 class="repo-remove-icon" aria-hidden="true" />
              </button>
            </div>
          )}
        </For>
      </div>
    </section>
  );
}

import { For, Show, createSignal } from "solid-js";
import type { RepoMark } from "./api";

export function RepoPicker(props: {
  repos: RepoMark[];
  error: string;
  onSelect: (repo: RepoMark) => void;
  onPullRequest: (url: string) => void | Promise<void>;
}) {
  const [pullRequestUrl, setPullRequestUrl] = createSignal("");
  const submitPullRequest = (event: SubmitEvent) => {
    event.preventDefault();
    void props.onPullRequest(pullRequestUrl());
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
            placeholder="https://github.com/org/repo/pull/123"
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

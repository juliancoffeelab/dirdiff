import {
  Show,
  createEffect,
  createMemo,
  createSignal,
  on,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";
import { Portal } from "solid-js/web";
import {
  createMutation,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { CircleUserRound } from "lucide-solid";
import { z } from "zod";
import { api, type Preferences, type UserProfile } from "../api/api";
import { Checkbox } from "../comp/Checkbox";
import { ErrorPopover, useToasts } from "../comp/Toasts";
import { assert, expect } from "../utils";

const PROFILE_STORAGE_KEY = "dirdiff:v1:profile";

const StoredProfileSchema = z.strictObject({
  id: z.number().int().positive(),
  username: z.string().min(1),
});

export type StoredProfile = z.infer<typeof StoredProfileSchema>;

type ProfileProps = {
  agentOnboardUrl: () => string | null;
  selected: StoredProfile | null;
  metadataTarget: HTMLElement | null;
  onSelected: (profile: StoredProfile) => void;
  onForgotten: () => void;
};

type ProfileUiState =
  | {
      view: "closed";
    }
  | {
      view: "menu";
    }
  | {
      view: "login";
      input: string;
    }
  | {
      view: "create";
      input: string;
    }
  | {
      view: "rename";
      input: string;
    }
  | {
      view: "preferences";
    };

type PreferencesModalProps = {
  profile: StoredProfile;
  onClose: () => void;
};

type PreferencesState =
  | {
      state: "pending";
    }
  | {
      state: "failed";
      error: Error;
    }
  | {
      state: "available";
      preferences: Preferences;
    };

type PreferencesEditorProps = {
  profile: StoredProfile;
  preferences: Preferences;
  onClose: () => void;
};

export function loadStoredProfile(): StoredProfile | null {
  const raw = window.localStorage.getItem(PROFILE_STORAGE_KEY);
  return raw === null ? null : StoredProfileSchema.parse(JSON.parse(raw));
}

export function Profile(props: ProfileProps): JSX.Element {
  const toast = useToasts();
  function storeProfile(profile: StoredProfile): void {
    window.localStorage.setItem(
      PROFILE_STORAGE_KEY,
      JSON.stringify(StoredProfileSchema.parse(profile)),
    );
  }

  let root!: HTMLDivElement;
  let trigger!: HTMLButtonElement;
  const [ui, setUi] = createSignal<ProfileUiState>({ view: "closed" });

  const loginProfile = createMutation(() => ({
    ...api.profile.login(),
    onSuccess(profile: UserProfile) {
      storeProfile(profile);
      props.onSelected(profile);
      setUi({ view: "closed" });
    },
  }));
  const registerProfile = createMutation(() => ({
    ...api.profile.register(),
    onSuccess(profile: UserProfile) {
      storeProfile(profile);
      props.onSelected(profile);
      setUi({ view: "closed" });
    },
  }));
  const renameProfile = createMutation(() => ({
    ...api.profile.rename(),
    onSuccess(profile: UserProfile) {
      storeProfile(profile);
      props.onSelected(profile);
      setUi({ view: "closed" });
    },
  }));

  createEffect(
    on(
      () => ui().view,
      (view) => {
        if (view === "closed" || view === "preferences") {
          return;
        }

        function dismissOutside(event: PointerEvent): void {
          const target = event.target;
          if (target instanceof Node && !root.contains(target)) {
            setUi({ view: "closed" });
          }
        }

        function dismissWithKeyboard(event: KeyboardEvent): void {
          if (event.key === "Escape") {
            setUi({ view: "closed" });
            trigger.focus();
          }
        }

        document.addEventListener("pointerdown", dismissOutside);
        document.addEventListener("keydown", dismissWithKeyboard);
        onCleanup(() => {
          document.removeEventListener("pointerdown", dismissOutside);
          document.removeEventListener("keydown", dismissWithKeyboard);
        });
      },
    ),
  );

  function submitUsername(): void {
    const state = ui();
    assert(
      state.view === "login" ||
        state.view === "create" ||
        state.view === "rename",
      "Username submission requires the username editor.",
    );
    if (state.view === "login") {
      loginProfile.mutate(state.input);
    } else if (state.view === "create") {
      registerProfile.mutate(state.input);
    } else {
      const selected = expect(
        props.selected,
        "Profile rename requires a selected Profile.",
      );
      renameProfile.mutate({
        profileId: selected.id,
        username: state.input,
      });
    }
  }

  function activeProfileMutation():
    | typeof loginProfile
    | typeof registerProfile
    | typeof renameProfile {
    const state = ui();
    if (state.view === "login") {
      return loginProfile;
    }
    if (state.view === "create") {
      return registerProfile;
    }
    if (state.view === "rename") {
      return renameProfile;
    }
    assert(false, "Profile mutation requires a username action.");
  }

  function usernameInput(): string {
    const state = ui();
    assert(
      state.view === "login" ||
        state.view === "create" ||
        state.view === "rename",
      "Username editor state changed unexpectedly.",
    );
    return state.input;
  }

  return (
    <div
      ref={root}
      class="profile-menu"
      data-open={
        ui().view !== "closed" && ui().view !== "preferences" ? "true" : "false"
      }
    >
      <button
        ref={trigger}
        type="button"
        class="profile-trigger"
        aria-haspopup="menu"
        aria-expanded={ui().view !== "closed" && ui().view !== "preferences"}
        aria-label="Profile"
        title="Profile"
        onClick={() =>
          setUi(ui().view === "closed" ? { view: "menu" } : { view: "closed" })
        }
      >
        <CircleUserRound class="profile-trigger-icon" aria-hidden="true" />
      </button>
      <Show when={ui().view !== "closed" && ui().view !== "preferences"}>
        <div class="profile-popover" role="menu" aria-label="Profile">
          <div class="profile-popover-header">
            <CircleUserRound class="profile-popover-icon" aria-hidden="true" />
            <div class="profile-popover-copy">
              <strong>{props.selected?.username ?? "___"}</strong>
              <span>Profile</span>
            </div>
          </div>
          <div class="profile-popover-divider" />
          <button
            type="button"
            class="profile-popover-option profile-popover-option-agent"
            role="menuitem"
            disabled={props.agentOnboardUrl() === null}
            title={
              props.agentOnboardUrl() === null
                ? "Load a supported Tab to copy its agent onboard link."
                : "Copy agent onboard link"
            }
            onClick={() => {
              const onboardUrl = expect(
                props.agentOnboardUrl(),
                "Agent onboarding requires a complete supported Tab.",
              );
              void navigator.clipboard
                .writeText(onboardUrl)
                .then(() => {
                  toast.showTransient(
                    "Agent onboard link copied",
                    "Paste the link into the agent conversation.",
                    2_000,
                  );
                  setUi({ view: "closed" });
                })
                .catch((error: unknown) =>
                  toast.showError("Could not copy agent onboard link", error),
                );
            }}
          >
            Copy agent onboard link
          </button>
          <div class="profile-popover-divider" />
          <Show
            when={
              ui().view === "login" ||
              ui().view === "create" ||
              ui().view === "rename"
            }
            fallback={
              <>
                <Show
                  when={props.selected !== null}
                  fallback={
                    <>
                      <button
                        type="button"
                        class="profile-popover-option"
                        role="menuitem"
                        onClick={() => setUi({ view: "login", input: "" })}
                      >
                        Log in
                      </button>
                      <button
                        type="button"
                        class="profile-popover-option"
                        role="menuitem"
                        onClick={() => setUi({ view: "create", input: "" })}
                      >
                        Create profile
                      </button>
                    </>
                  }
                >
                  <button
                    type="button"
                    class="profile-popover-option"
                    role="menuitem"
                    onClick={() => setUi({ view: "login", input: "" })}
                  >
                    Log in as another profile
                  </button>
                  <button
                    type="button"
                    class="profile-popover-option"
                    role="menuitem"
                    onClick={() =>
                      setUi({
                        view: "rename",
                        input: props.selected?.username ?? "",
                      })
                    }
                  >
                    Edit username
                  </button>
                  <button
                    type="button"
                    class="profile-popover-option"
                    role="menuitem"
                    onClick={() => setUi({ view: "preferences" })}
                  >
                    Preferences
                  </button>
                  <button
                    type="button"
                    class="profile-popover-option"
                    role="menuitem"
                    onClick={() => {
                      window.localStorage.removeItem(PROFILE_STORAGE_KEY);
                      props.onForgotten();
                      setUi({ view: "closed" });
                    }}
                  >
                    Log out
                  </button>
                </Show>
              </>
            }
          >
            <form
              class="profile-form"
              onSubmit={(event) => {
                // Keep the username input local and submit its completed value once.
                event.preventDefault();
                submitUsername();
              }}
            >
              <label class="profile-form-field">
                <span>Username</span>
                <input
                  type="text"
                  value={usernameInput()}
                  onInput={(event) => {
                    const state = ui();
                    assert(
                      state.view === "login" ||
                        state.view === "create" ||
                        state.view === "rename",
                      "Username input requires a username action.",
                    );
                    setUi({
                      view: state.view,
                      input: event.currentTarget.value,
                    });
                  }}
                  disabled={activeProfileMutation().isPending}
                />
              </label>
              <Show when={activeProfileMutation().error} keyed>
                {(error) => (
                  <ErrorPopover
                    title="Profile action failed"
                    error={error}
                    onRetry={submitUsername}
                    trigger={<span>Profile action failed</span>}
                    triggerClass="profile-form-error compact-error-trigger"
                    triggerLabel="Show profile error"
                  />
                )}
              </Show>
              <div class="profile-form-actions">
                <button
                  type="submit"
                  class="profile-form-submit"
                  disabled={activeProfileMutation().isPending}
                >
                  {activeProfileMutation().isPending
                    ? "Working..."
                    : ui().view === "login"
                      ? "Log in"
                      : ui().view === "create"
                        ? "Create profile"
                        : "Save"}
                </button>
                <button
                  type="button"
                  class="profile-form-cancel"
                  disabled={activeProfileMutation().isPending}
                  onClick={() => setUi({ view: "menu" })}
                >
                  Cancel
                </button>
              </div>
            </form>
          </Show>
        </div>
      </Show>
      <Show when={ui().view === "preferences"}>
        <Show when={props.selected} keyed>
          {(selected) => (
            <PreferencesModal
              profile={selected}
              onClose={() => {
                setUi({ view: "closed" });
                trigger.focus();
              }}
            />
          )}
        </Show>
      </Show>
      <ProfilePreferencesStatus
        profile={props.selected}
        metadataTarget={props.metadataTarget}
      />
    </div>
  );
}

type ProfilePreferencesStatusProps = {
  profile: StoredProfile | null;
  metadataTarget: HTMLElement | null;
};

function ProfilePreferencesStatus(
  props: ProfilePreferencesStatusProps,
): JSX.Element {
  const preferences = createQuery(() => ({
    ...api.profile.preferences(props.profile?.id ?? null),
  }));

  return (
    <Show when={props.metadataTarget} keyed>
      {(target) => (
        <Show when={preferences.isPending || preferences.error !== null}>
          <Portal mount={target}>
            <div class="summary-group summary-group-status summary-status-preferences">
              <Show
                when={preferences.error}
                keyed
                fallback={<span>Loading preferences...</span>}
              >
                {(error) => (
                  <ErrorPopover
                    title="Failed to load preferences"
                    error={error}
                    onRetry={() => void preferences.refetch()}
                    trigger={<span>Failed to load preferences</span>}
                    triggerClass="compact-error-trigger summary-error-trigger"
                    triggerLabel="Show preferences error"
                  />
                )}
              </Show>
            </div>
          </Portal>
        </Show>
      )}
    </Show>
  );
}

function PreferencesModal(props: PreferencesModalProps): JSX.Element {
  const preferences = createQuery(() => ({
    ...api.profile.preferences(props.profile.id),
  }));
  const state = createMemo<PreferencesState>(() => {
    if (preferences.error !== null) {
      return { state: "failed", error: preferences.error };
    }
    if (preferences.isPending) {
      return { state: "pending" };
    }
    return {
      state: "available",
      preferences: expect(
        preferences.data,
        "A settled preferences query requires data or an explicit error.",
      ),
    };
  });
  const loadError = createMemo(() => {
    const current = state();
    return current.state === "failed" ? current.error : null;
  });
  const loadedPreferences = createMemo(() => {
    const current = state();
    return current.state === "available" ? current.preferences : null;
  });

  onMount(() => {
    function dismissWithKeyboard(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        props.onClose();
      }
    }

    document.addEventListener("keydown", dismissWithKeyboard);
    onCleanup(() => {
      document.removeEventListener("keydown", dismissWithKeyboard);
    });
  });

  return (
    <div class="help-modal-backdrop" onClick={props.onClose}>
      <section
        class="help-modal profile-preferences-modal"
        aria-label="Preferences"
        onClick={(event) => event.stopPropagation()}
      >
        <div class="help-modal-header">
          <strong>Preferences</strong>
          <button type="button" onClick={props.onClose}>
            Close
          </button>
        </div>
        <Show when={loadError()} keyed>
          {(error) => (
            <div class="profile-preferences-error-block">
              <ErrorPopover
                title="Failed to load preferences"
                error={error}
                onRetry={() => void preferences.refetch()}
                trigger={<span>Failed to load preferences</span>}
                triggerClass="profile-form-error compact-error-trigger"
                triggerLabel="Show preferences error"
              />
            </div>
          )}
        </Show>
        <Show when={state().state === "pending"}>
          <p class="profile-preferences-message">Loading preferences...</p>
        </Show>
        <Show when={loadedPreferences()} keyed>
          {(loaded) => (
            <PreferencesEditor
              profile={props.profile}
              preferences={loaded}
              onClose={props.onClose}
            />
          )}
        </Show>
      </section>
    </div>
  );
}

function PreferencesEditor(props: PreferencesEditorProps): JSX.Element {
  const queryClient = useQueryClient();
  const [aggressiveFolds, setAggressiveFolds] = createSignal(
    props.preferences.aggressive_folds,
  );
  const savePreferences = createMutation(() =>
    api.profile.savePreferences(queryClient),
  );

  function submitPreferences(): void {
    savePreferences.mutate(
      {
        profileId: props.profile.id,
        aggressiveFolds: aggressiveFolds(),
        gitAlgorithm: props.preferences.git_algorithm,
        gitColorMoved: props.preferences.git_color_moved,
        gitColorMovedWs: props.preferences.git_color_moved_ws,
        gitIgnoreSpaceChange: props.preferences.git_ignore_space_change,
        gitWordDiff: props.preferences.git_word_diff,
        gitWordBoundary: props.preferences.git_word_boundary,
        gitWordDiffRegex: props.preferences.git_word_diff_regex,
        difftasticIgnoreComments: props.preferences.difftastic_ignore_comments,
        gumtreeMatcher: props.preferences.gumtree_matcher,
      },
      { onSuccess: props.onClose },
    );
  }

  return (
    <form
      class="profile-preferences-form"
      onSubmit={(event) => {
        // Submit the local checkbox value without copying backend state upward.
        event.preventDefault();
        submitPreferences();
      }}
    >
      <Checkbox
        class="profile-checkbox"
        label="Aggressive folds"
        description="Fold unchanged regions when fold hints exist."
        checked={aggressiveFolds()}
        busy={savePreferences.isPending}
        onToggle={setAggressiveFolds}
      />
      <Show when={savePreferences.error} keyed>
        {(error) => (
          <ErrorPopover
            title="Failed to save preferences"
            error={error}
            onRetry={submitPreferences}
            trigger={<span>Failed to save preferences</span>}
            triggerClass="profile-form-error compact-error-trigger"
            triggerLabel="Show preferences save error"
          />
        )}
      </Show>
      <div class="profile-form-actions profile-preferences-actions">
        <button
          type="submit"
          class="profile-form-submit"
          disabled={savePreferences.isPending}
        >
          {savePreferences.isPending ? "Saving..." : "Save"}
        </button>
        <button
          type="button"
          class="profile-form-cancel"
          disabled={savePreferences.isPending}
          onClick={props.onClose}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

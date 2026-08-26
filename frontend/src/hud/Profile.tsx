/**
 * Defines profile identity, profile-local persistence, and preferences UI.
 *
 * The module exports the Profile HUD component and the one startup reader used
 * by App. Profile stores menu/dialog state, performs explicit localStorage writes,
 * profile mutations, preference observation, and preference mutation. It does
 * not place profile data in workspace URLs or copy backend preferences into App.
 */
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
import { ErrorPopover } from "../comp/Toasts";
import { assert, expect } from "../utils";

const PROFILE_STORAGE_KEY = "dirdiff:v1:profile";
const StoredProfileSchema = z.strictObject({
  id: z.number().int().positive(),
  username: z.string().min(1),
});

/**
 * Represents the selected profile identity persisted by this browser.
 *
 * It is exactly the validated backend profile shape. Canonical preferences and
 * transient menu state are excluded because they have separate storage locations
 * and lifetimes.
 */
export type StoredProfile = z.infer<typeof StoredProfileSchema>;

/**
 * Defines the complete inputs and profile-selection operations supplied by App.
 *
 * App provides the selected identity or explicit null. Successful mutations and
 * login, creation, rename, and logout return complete selection changes;
 * Profile performs persistence before notifying App and never exposes partial
 * username input.
 */
type ProfileProps = {
  selected: StoredProfile | null;
  metadataTarget: HTMLElement | null;
  onSelected: (profile: StoredProfile) => void;
  onForgotten: () => void;
};

/**
 * Represents every mutually exclusive local presentation state of Profile.
 *
 * Username and preferences variants contain their complete editable input.
 * Backend pending and error state deliberately remains in TanStack observers.
 */
type ProfileUiState =
  | { view: "closed" }
  | { view: "menu" }
  | { view: "login"; input: string }
  | { view: "create"; input: string }
  | { view: "rename"; input: string }
  | { view: "preferences" };

/**
 * Defines the required inputs of the private preferences dialog.
 *
 * A concrete profile is mandatory because preferences have no profile-less
 * query identity. Closing returns to Profile without changing identity.
 */
type PreferencesModalProps = {
  profile: StoredProfile;
  onClose: () => void;
};

/**
 * Represents the complete observable state of one profile's preferences query.
 *
 * Pending and failed variants contain no preferences entity, so retained query
 * data cannot keep the editor alive after a failed load or refetch. Available is
 * the only variant from which PreferencesEditor may be constructed.
 */
type PreferencesState =
  | { state: "pending" }
  | { state: "failed"; error: Error }
  | { state: "available"; preferences: Preferences };

/**
 * Defines the required inputs of the private editable preferences form.
 *
 * The validated backend entity seeds component-local input exactly once. A
 * concrete profile is required to address the save mutation and cache entry.
 */
type PreferencesEditorProps = {
  profile: StoredProfile;
  preferences: Preferences;
  onClose: () => void;
};

/**
 * Loads the explicitly persisted selected profile for application startup.
 *
 * Callers receive null when no selection exists. Stored content is parsed
 * strictly and malformed data throws rather than creating a substitute identity.
 */
export function loadStoredProfile(): StoredProfile | null {
  const raw = window.localStorage.getItem(PROFILE_STORAGE_KEY);
  return raw === null ? null : StoredProfileSchema.parse(JSON.parse(raw));
}

/**
 * Renders the profile trigger, menu, username workflow, and preferences dialog.
 *
 * The caller stores only selected identity. Profile stores all transient
 * interaction state, performs backend mutations, persists confirmed identity
 * explicitly, and reports complete selection changes only after successful
 * backend responses.
 */
export function Profile(props: ProfileProps): JSX.Element {
  /**
   * Replaces the browser's selected-profile record with one validated identity.
   *
   * Callers provide a complete backend-confirmed profile. The operation does not
   * persist preferences or update reactive App state by itself.
   */
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
    /** Persists and publishes the exact existing Profile selected by name. */
    onSuccess(profile: UserProfile) {
      storeProfile(profile);
      props.onSelected(profile);
      setUi({ view: "closed" });
    },
  }));
  const registerProfile = createMutation(() => ({
    ...api.profile.register(),
    /**
     * Applies the authoritative profile returned by successful registration.
     *
     * TanStack invokes this only after backend success. The callback persists the
     * complete identity, reports it to App, and closes the transient workflow.
     */
    onSuccess(profile: UserProfile) {
      storeProfile(profile);
      props.onSelected(profile);
      setUi({ view: "closed" });
    },
  }));
  const renameProfile = createMutation(() => ({
    ...api.profile.rename(),
    /**
     * Applies the authoritative profile returned by a successful rename.
     *
     * TanStack invokes this only after backend success. The callback replaces
     * persisted and App-selected identity together, then closes the workflow.
     */
    onSuccess(profile: UserProfile) {
      storeProfile(profile);
      props.onSelected(profile);
      setUi({ view: "closed" });
    },
  }));

  /**
   * Synchronizes the open profile-menu lifetime with document dismissal events.
   *
   * The effect explicitly tracks only the UI view. Entering the menu or a
   * username action installs one listener pair; leaving those states or
   * disposing Profile removes that pair before any later installation. The
   * preferences dialog handles Escape locally and is deliberately excluded.
   */
  createEffect(
    on(
      () => ui().view,
      (view) => {
        if (view === "closed" || view === "preferences") {
          return;
        }

        /**
         * Dismisses the menu when pointer interaction leaves its complete root.
         *
         * It discards only transient menu/input state and never changes profile data.
         */
        function dismissOutside(event: PointerEvent): void {
          const target = event.target;
          if (target instanceof Node && !root.contains(target)) {
            setUi({ view: "closed" });
          }
        }

        /**
         * Dismisses the menu on Escape and restores focus to the profile trigger.
         *
         * Other keys remain available to the active form and menu controls.
         */
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

  /**
   * Submits the explicit login, creation, or rename action selected by the user.
   */
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

  /**
   * Returns the mutation for the explicit username action currently presented.
   */
  function activeProfileMutation():
    | typeof loginProfile
    | typeof registerProfile
    | typeof renameProfile {
    const state = ui();
    if (state.view === "login") return loginProfile;
    if (state.view === "create") return registerProfile;
    if (state.view === "rename") return renameProfile;
    assert(false, "Profile mutation requires a username action.");
  }

  /**
   * Returns the complete username input from the active editor state.
   *
   * Rendering the username form outside that state is a programming error and
   * throws instead of substituting an empty value.
   */
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
      <Show when={props.selected} keyed>
        {(selected) => (
          <ProfilePreferencesStatus
            profile={selected}
            metadataTarget={props.metadataTarget}
          />
        )}
      </Show>
    </div>
  );
}

/**
 * Defines the selected identity and mounted Header outlet used for preference
 * status presentation.
 *
 * The outlet is genuinely null before AppHeader mounts it. The query remains
 * inside Profile regardless of physical status placement.
 */
type ProfilePreferencesStatusProps = {
  profile: StoredProfile;
  metadataTarget: HTMLElement | null;
};

/**
 * Observes selected-profile preferences and renders compact state in AppHeader.
 *
 * Preferences warm as soon as a profile is selected, matching the established
 * shell lifecycle. The component exposes no query data and portals only the
 * compact presentation; the dialog observes the same canonical cached query.
 */
function ProfilePreferencesStatus(
  props: ProfilePreferencesStatusProps,
): JSX.Element {
  const preferences = createQuery(() => ({
    ...api.profile.preferences(props.profile.id),
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

/**
 * Renders and performs backend interaction for one selected profile's preferences.
 *
 * Opening observes the exact profile query. Saving writes only the backend-
 * confirmed response into that same cache entry. Errors remain compact in the
 * dialog and open complete details with explicit retry.
 */
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

  /**
   * Binds Escape dismissal to the exact mounted lifetime of this modal.
   *
   * PreferencesModal exists only while the dialog is open, so a one-shot,
   * non-tracking mount hook is sufficient. Its cleanup removes the document
   * listener when the conditional modal closes or is otherwise disposed.
   */
  onMount(() => {
    /**
     * Closes the preferences dialog on Escape through the supplied callback.
     *
     * Other keys remain available to the checkbox and form controls.
     */
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

/**
 * Renders the editable form for one already-loaded preferences entity.
 *
 * The backend value seeds one component-local checkbox at mount. The editor
 * performs no prop synchronization and writes only a confirmed mutation result
 * into the canonical preferences cache before closing.
 */
function PreferencesEditor(props: PreferencesEditorProps): JSX.Element {
  const queryClient = useQueryClient();
  const [aggressiveFolds, setAggressiveFolds] = createSignal(
    props.preferences.aggressive_folds,
  );
  const savePreferences = createMutation(() => ({
    ...api.profile.savePreferences(),
    /**
     * Applies preferences confirmed by the backend to the canonical query entry.
     *
     * TanStack supplies the complete saved value after success. The callback
     * writes that exact value and closes the editor without copying it into
     * separate Profile state.
     */
    onSuccess(saved: Preferences) {
      queryClient.setQueryData(
        api.profile.preferences(props.profile.id).queryKey,
        saved,
      );
      props.onClose();
    },
  }));

  /**
   * Submits the complete preferences entity for this required profile.
   *
   * The current checkbox value is sent without optimistic cache mutation. A
   * successful backend result closes the dialog through the mutation callback.
   */
  function submitPreferences(): void {
    savePreferences.mutate({
      profileId: props.profile.id,
      aggressiveFolds: aggressiveFolds(),
    });
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
      <label class="profile-checkbox-row">
        <span class="profile-checkbox-copy">
          <strong>Aggressive folds</strong>
          <span>Fold unchanged regions when fold hints exist.</span>
        </span>
        <span class="profile-checkbox-control">
          <input
            class="profile-checkbox-input"
            type="checkbox"
            checked={aggressiveFolds()}
            disabled={savePreferences.isPending}
            onInput={(event) => setAggressiveFolds(event.currentTarget.checked)}
          />
          <span
            class="visibility-indicator large profile-checkbox-indicator"
            classList={{ visible: aggressiveFolds() }}
            aria-hidden="true"
          />
        </span>
      </label>
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

/**
 * Defines profile identity, profile-local persistence, and preferences UI.
 *
 * The module exports the Profile HUD component and the one startup reader used
 * by its App owner. It owns menu/dialog state, explicit localStorage writes,
 * profile mutations, preference observation, and preference mutation. It does
 * not place profile data in workspace URLs or copy backend preferences into App.
 */
import {
  Show,
  createEffect,
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

const PROFILE_STORAGE_KEY = "dirdiff:v1:profile";
const StoredProfileSchema = z.strictObject({
  id: z.number().int().positive(),
  username: z.string().min(1),
});

/**
 * Represents the selected profile identity persisted by this browser.
 *
 * It is exactly the validated backend profile shape. Preferences and transient
 * menu state are excluded because they have different owners and lifetimes.
 */
export type StoredProfile = z.infer<typeof StoredProfileSchema>;

/**
 * Defines the complete App-owned inputs and profile-selection operations.
 *
 * App provides the selected identity or explicit null. Successful mutations and
 * forgetting return complete selection changes; Profile performs persistence
 * before notifying App and never exposes partial username input.
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
 * Username and preferences variants contain their complete editable draft.
 * Backend pending and error state deliberately remains in TanStack observers.
 */
type ProfileUiState =
  | { view: "closed" }
  | { view: "menu" }
  | { view: "username"; input: string }
  | { view: "preferences" };

/**
 * Defines the required inputs of the private preferences dialog.
 *
 * A concrete profile is mandatory because preferences have no profile-less
 * query identity. Closing returns ownership to Profile without changing identity.
 */
type PreferencesModalProps = {
  profile: StoredProfile;
  onClose: () => void;
};

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

/**
 * Renders the profile trigger, menu, username workflow, and preferences dialog.
 *
 * The caller owns only selected identity. Profile owns all transient interaction,
 * performs backend mutations, persists confirmed identity explicitly, and reports
 * complete selection changes only after successful backend responses.
 */
export function Profile(props: ProfileProps): JSX.Element {
  let root!: HTMLDivElement;
  let trigger!: HTMLButtonElement;
  const [ui, setUi] = createSignal<ProfileUiState>({ view: "closed" });

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

  /**
   * Synchronizes the open profile-menu lifetime with document dismissal events.
   *
   * The effect explicitly tracks only the UI view. Entering `menu` or `username`
   * installs one listener pair; leaving either state, replacing the view, or
   * disposing Profile removes that pair before any later installation. The
   * preferences dialog owns its own Escape lifecycle and is deliberately excluded.
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
   * Submits the current username draft through the correct backend mutation.
   *
   * A missing selected profile registers a new identity; an existing identity is
   * renamed by exact ID. Empty or invalid usernames remain backend-visible errors.
   */
  function submitUsername(): void {
    const state = ui();
    if (state.view !== "username") {
      throw new Error("Username submission requires the username editor.");
    }
    const selected = props.selected;
    if (selected === null) {
      registerProfile.mutate(state.input);
    } else {
      renameProfile.mutate({ profileId: selected.id, username: state.input });
    }
  }

  /**
   * Returns the active username mutation selected by profile presence.
   *
   * Registration and rename share presentation but retain separate backend
   * contracts; callers may inspect their common pending and error state only.
   */
  function activeProfileMutation():
    | typeof registerProfile
    | typeof renameProfile {
    return props.selected === null ? registerProfile : renameProfile;
  }

  /**
   * Returns the complete username draft from the active editor state.
   *
   * Rendering the username form outside that state is a programming error and
   * throws instead of substituting an empty value.
   */
  function usernameDraft(): string {
    const state = ui();
    if (state.view !== "username") {
      throw new Error("Username editor state changed unexpectedly.");
    }
    return state.input;
  }

  return (
    <div
      ref={root}
      class="profile-menu"
      data-open={
        ui().view === "menu" || ui().view === "username" ? "true" : "false"
      }
    >
      <button
        ref={trigger}
        type="button"
        class="profile-trigger"
        aria-haspopup="menu"
        aria-expanded={ui().view === "menu" || ui().view === "username"}
        aria-label="Profile"
        title="Profile"
        onClick={() =>
          setUi(ui().view === "closed" ? { view: "menu" } : { view: "closed" })
        }
      >
        <CircleUserRound class="profile-trigger-icon" aria-hidden="true" />
      </button>
      <Show when={ui().view === "menu" || ui().view === "username"}>
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
            when={ui().view === "username"}
            fallback={
              <>
                <button
                  type="button"
                  class="profile-popover-option"
                  role="menuitem"
                  onClick={() =>
                    setUi({
                      view: "username",
                      input: props.selected?.username ?? "",
                    })
                  }
                >
                  {props.selected === null ? "Log in" : "Edit username"}
                </button>
                <Show when={props.selected !== null}>
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
                      // Forgetting removes browser persistence, not backend data.
                      window.localStorage.removeItem(PROFILE_STORAGE_KEY);
                      props.onForgotten();
                      setUi({ view: "closed" });
                    }}
                  >
                    Forget local profile
                  </button>
                </Show>
              </>
            }
          >
            <form
              class="profile-form"
              onSubmit={(event) => {
                // Keep the username draft local and submit its completed value once.
                event.preventDefault();
                submitUsername();
              }}
            >
              <label class="profile-form-field">
                <span>Username</span>
                <input
                  type="text"
                  value={usernameDraft()}
                  onInput={(event) =>
                    setUi({
                      view: "username",
                      input: event.currentTarget.value,
                    })
                  }
                  disabled={activeProfileMutation().isPending}
                />
              </label>
              <Show when={activeProfileMutation().error} keyed>
                {(error) => (
                  <ErrorPopover
                    title="Failed to save profile"
                    error={error}
                    onRetry={submitUsername}
                    trigger={<span>Failed to save profile</span>}
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
                    ? "Saving..."
                    : props.selected === null
                      ? "Create"
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
 * status projection.
 *
 * The outlet is genuinely null before AppHeader mounts it. Query ownership remains
 * inside Profile regardless of physical status placement.
 */
type ProfilePreferencesStatusProps = {
  profile: StoredProfile;
  metadataTarget: HTMLElement | null;
};

/**
 * Observes selected-profile preferences and projects compact state to AppHeader.
 *
 * Preferences warm as soon as a profile is selected, matching the established
 * shell lifecycle. The component exposes no data and Portals presentation only;
 * the dialog observes the same canonical cached query.
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
 * Renders and owns backend interaction for one selected profile's preferences.
 *
 * Opening observes the exact profile query. Saving writes only the backend-
 * confirmed response into that same cache entry. Errors remain compact in the
 * dialog and open complete details with explicit retry.
 */
function PreferencesModal(props: PreferencesModalProps): JSX.Element {
  const preferences = createQuery(() => ({
    ...api.profile.preferences(props.profile.id),
  }));

  /**
   * Binds Escape dismissal to the exact mounted lifetime of this modal.
   *
   * PreferencesModal exists only while the dialog is open, so a one-shot,
   * non-tracking mount hook is sufficient. Its cleanup removes the document
   * listener when the conditional owner closes or otherwise disposes the modal.
   */
  onMount(() => {
    /**
     * Closes the preferences dialog on Escape through its owner callback.
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
        <Show when={preferences.isPending}>
          <p class="profile-preferences-message">Loading preferences...</p>
        </Show>
        <Show when={preferences.error} keyed>
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
        <Show when={preferences.data} keyed>
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
 * The backend value seeds one component-owned checkbox at mount. The editor
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
          <span>Collapse unchanged regions when fold hints exist.</span>
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

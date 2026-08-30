/**
 * Manages browser Profile selection and the selected Profile's preferences UI.
 *
 * `Profile` owns its menu and form state. Successful identity operations validate
 * and persist the complete Profile before reporting it to `App`; logout removes
 * that persisted identity first. Preferences remain canonical query data and the
 * modal keeps only its current editable input.
 *
 * Profile identity does not enter workspace URLs, and preferences are never
 * copied into application or workspace state.
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
import { ErrorPopover, useToasts } from "../comp/Toasts";
import { assert, expect } from "../utils";

/**
 * Names the browser record containing the last confirmed Profile identity.
 *
 * The version is part of the persisted format boundary. Readers validate the
 * associated value strictly; this module neither searches older keys nor repairs
 * malformed content.
 */
const PROFILE_STORAGE_KEY = "dirdiff:v1:profile";

/**
 * Validates the complete identity allowed across the backend, App, and storage boundaries.
 *
 * Strict validation keeps preferences and undeclared data out of the persisted
 * record. A value must have a positive database ID and a nonempty username before
 * it can become the selected Profile.
 */
const StoredProfileSchema = z.strictObject({
  /** Positive backend identity used by Profile and preference operations. */
  id: z.number().int().positive(),
  /** Non-empty backend-confirmed name displayed as the selected Profile. */
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
  /**
   * Returns the absolute onboarding URL for the current complete supported Tab.
   *
   * `null` disables the copy action for Preset and incomplete workflows. Profile
   * reads the accessor only for presentation and explicit clipboard activation.
   *
   * # Returns
   *
   * - Absolute onboarding endpoint copied by explicit activation.
   * - `null` when the action must remain disabled for the current Tab.
   */
  agentOnboardUrl: () => string | null;
  /**
   * Confirmed Profile identity controlled by App, or genuine absence.
   *
   * Profile reads the current value for display, rename, preferences, and logout;
   * it does not retain a second selected identity after a callback.
   */
  selected: StoredProfile | null;
  /**
   * Mounted AppHeader destination for compact preference-query status.
   *
   * `null` before header registration suppresses only Portal presentation. The
   * selected Profile's canonical preferences observer remains mounted.
   */
  metadataTarget: HTMLElement | null;
  /**
   * Accepts the exact backend Profile after login, registration, or rename succeeds.
   *
   * Profile first validates and writes that identity to localStorage, then invokes
   * this callback before closing the workflow. It does not run for submission
   * failure, cancellation, preferences changes, or logout. The caller may update
   * application state and must pass the accepted identity back through `selected`
   * for the mounted control to reflect it.
   */
  onSelected: (profile: StoredProfile) => void;
  /**
   * Reports an explicit logout after the persisted identity has been removed.
   *
   * The callback receives no replacement Profile and does not run for menu
   * dismissal or failed mutations. The caller must clear its controlled
   * `selected` value; after the callback returns, Profile closes the menu.
   */
  onForgotten: () => void;
};

/**
 * Represents every mutually exclusive local presentation state of Profile.
 *
 * Username and preferences variants contain their complete editable input.
 * Backend pending and error state deliberately remains in TanStack observers.
 */
type ProfileUiState =
  | {
      /**
       * No Profile popup or modal is rendered.
       *
       * Document dismissal listeners are inactive in this state.
       */
      view: "closed";
    }
  | {
      /**
       * The choice menu is open without an active username operation.
       *
       * The next action either closes it or replaces this state with one workflow.
       */
      view: "menu";
    }
  | {
      /**
       * The username form will select an existing exact-name Profile.
       *
       * Submission addresses the login mutation and never creates a new identity.
       */
      view: "login";
      /**
       * Current local username text submitted unchanged to the login mutation.
       *
       * Failed submission retains it for correction or explicit retry.
       */
      input: string;
    }
  | {
      /**
       * The username form will register a new Profile.
       *
       * Submission cannot select an existing exact-name identity through this arm.
       */
      view: "create";
      /**
       * Current local username text submitted unchanged to registration.
       *
       * Failed submission retains it for correction or explicit retry.
       */
      input: string;
    }
  | {
      /**
       * The username form will rename the currently selected Profile.
       *
       * Entering this state requires a selected identity whose ID remains authoritative.
       */
      view: "rename";
      /**
       * Current local username text, initially seeded from the selected identity.
       *
       * Editing changes only this transient value until backend success.
       */
      input: string;
    }
  | {
      /**
       * The selected Profile's preferences modal replaces the menu presentation.
       *
       * The modal may exist only while the controlled selected identity is present.
       */
      view: "preferences";
    };

/**
 * Defines the required inputs of the private preferences dialog.
 *
 * A concrete profile is mandatory because preferences have no profile-less
 * query identity. Closing returns to Profile without changing identity.
 */
type PreferencesModalProps = {
  /**
   * Concrete selected identity whose ID addresses the canonical preferences query.
   *
   * The keyed caller fixes this Profile for the modal's mounted lifetime; identity
   * changes replace the modal instead of redirecting its observer.
   */
  profile: StoredProfile;
  /**
   * Closes this modal after backdrop, Close, Escape, Cancel, or successful save.
   *
   * It does not run for interaction inside the dialog, loading, or save failure.
   * The caller may replace modal state and restore trigger focus; once invoked,
   * the modal and its document listener are disposed.
   */
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
  | {
      /**
       * The canonical query has not produced a current preferences entity.
       *
       * No editor or retained value may be read from this arm.
       */
      state: "pending";
    }
  | {
      /**
       * The current observation failed, so the editor must not use retained data.
       *
       * The dialog exposes the failure and explicit refetch instead.
       */
      state: "failed";
      /**
       * Exact query failure shown by the dialog's retryable error presentation.
       *
       * This derived arm retains the canonical preferences query observer's
       * failure without wrapping or copying it.
       */
      error: Error;
    }
  | {
      /**
       * A validated current preferences entity is available to mount the editor.
       *
       * This is the only arm permitted to expose editable values.
       */
      state: "available";
      /**
       * Complete backend value used to seed one editor lifetime.
       *
       * Later edits remain local until backend-confirmed save.
       */
      preferences: Preferences;
    };

/**
 * Defines the required inputs of the private editable preferences form.
 *
 * The validated backend entity seeds component-local input exactly once. A
 * concrete profile is required to address the save mutation and cache entry.
 */
type PreferencesEditorProps = {
  /**
   * Selected identity whose ID addresses both save input and cache publication.
   *
   * It remains fixed for this editor mount; the preferences value comes from the
   * canonical query addressed by the same ID.
   */
  profile: StoredProfile;
  /**
   * Validated backend preferences used once to seed local editable controls.
   *
   * Later cache changes do not overwrite edits in this mounted form; remounting
   * creates a new editing lifetime from the newly supplied entity.
   */
  preferences: Preferences;
  /**
   * Ends editing after Cancel or a backend-confirmed save.
   *
   * Failed and pending saves do not invoke it. The caller may close the modal and
   * restore focus; successful save publishes canonical cache data first.
   */
  onClose: () => void;
};

/**
 * Loads the explicitly persisted selected profile for application startup.
 *
 * The operation reads the browser record once and returns `null` only when the
 * key is absent. It performs no backend lookup and does not clear or repair a
 * malformed record.
 *
 * # Usage
 *
 * `App` calls this while creating its selected-Profile signal. Later identity
 * changes arrive through `Profile` callbacks instead of repeated storage reads.
 *
 * # Returns
 *
 * - `StoredProfile`: The validated identity stored under the selected-Profile
 *   key.
 * - `null`: The key is absent. Startup must keep Profile selection empty until
 *   an explicit login or creation action succeeds.
 *
 * # Failures
 *
 * Storage access, JSON decoding, and strict Profile validation failures propagate
 * to the application boundary rather than creating a substitute identity.
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
  const toast = useToasts();
  /**
   * Replaces the browser's selected-profile record with one validated identity.
   *
   * Callers provide a complete backend-confirmed profile. The operation does not
   * persist preferences or update reactive App state by itself.
   *
   * @param profile Complete backend-confirmed identity to persist.
   *
   * # Failures
   *
   * Strict validation and localStorage write failures propagate before the caller
   * can report the identity to App.
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
    /**
     * Applies the existing Profile returned by successful login.
     *
     * TanStack invokes this only after the backend accepts the exact-name login.
     * The callback validates and persists the complete identity, reports it to
     * App, and then closes the transient username workflow. Login failure and
     * cancellation never enter this callback.
     *
     * @param profile Existing Profile returned by the accepted login mutation.
     *
     * # Failures
     *
     * Persistence or `onSelected` failure becomes this mutation's error before the
     * workflow closes, so the mounted form presents it through the same observer.
     */
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
     *
     * @param profile Newly registered Profile returned by the backend.
     *
     * # Failures
     *
     * Persistence or `onSelected` failure becomes this mutation's error before the
     * workflow closes, so the mounted form presents it through the same observer.
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
     *
     * @param profile Renamed Profile returned by the accepted mutation.
     *
     * # Failures
     *
     * Persistence or `onSelected` failure becomes this mutation's error before the
     * workflow closes, so the mounted form presents it through the same observer.
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
   *
   * The current editor arm chooses exactly one mutation and supplies its input
   * unchanged. Login and creation use only the typed name. Rename combines that
   * name with the currently controlled Profile identity. Backend rejection and
   * success-publication failure remain on the selected mutation observer, leaving
   * the editor and its input mounted.
   *
   * # Usage
   *
   * The username form and its retry control call this only in `login`, `create`,
   * or `rename` state. The form disables its controls while that mutation reports
   * pending.
   *
   * # Failures
   *
   * Calling outside a username editor throws. Rename also throws when App has not
   * supplied the selected Profile required to address the mutation.
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
   *
   * The username form reads this observer for pending, error, retry, and action
   * labels. It returns the same mutation that `submitUsername` starts for the
   * current editor arm and never chooses a default action.
   *
   * # Usage
   *
   * Call only while rendering the login, creation, or rename form.
   *
   * # Returns
   *
   * - Login view returns the existing login mutation, whose status and retry
   *   belong to username lookup.
   * - Creation view returns the existing registration mutation, whose status
   *   and retry belong to creating the entered username.
   * - Rename view returns the existing rename mutation, whose status and retry
   *   belong to changing the selected Profile. The form reads only the returned
   *   action and never combines state from the other two.
   *
   * # Failures
   *
   * Other Profile views throw because they do not have a username mutation.
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
  /**
   * Confirmed selected identity whose ID fixes the observed preferences query key.
   *
   * The keyed parent replaces this component when the selected Profile changes.
   */
  profile: StoredProfile;
  /**
   * Current AppHeader Portal destination, or null before it is registered.
   *
   * Absence suppresses compact status rendering only; it does not disable or
   * relocate the canonical query observer.
   */
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
     *
     * @param saved Complete backend-confirmed preferences entity.
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

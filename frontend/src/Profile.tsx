import { Show, createEffect, createSignal, onCleanup } from "solid-js";
import { CircleUserRound } from "lucide-solid";
import {
  createUserProfile,
  updatePreferences,
  updateUserProfile,
  type Preferences,
  type UserProfile,
} from "./api";
import { useToasts } from "./Toasts";
import {
  clearStoredProfile,
  loadStoredProfile,
  saveStoredProfile,
  toStoredProfile,
  type StoredProfile,
} from "./storage";

export function Profile(props: {
  preferences: Preferences | null;
  preferencesPending: boolean;
  preferencesError: string | null;
  onPreferencesSaved: (preferences: Preferences) => void;
  onReloadPreferences: () => Promise<void> | void;
}) {
  let root: HTMLDivElement | undefined;
  let trigger: HTMLButtonElement | undefined;
  const [open, setOpen] = createSignal(false);
  const [preferencesOpen, setPreferencesOpen] = createSignal(false);
  const [storedProfile, setStoredProfile] = createSignal<StoredProfile | null>(
    loadStoredProfile(),
  );
  const [editing, setEditing] = createSignal(false);
  const [draftUsername, setDraftUsername] = createSignal("");
  const [draftAggressiveFolds, setDraftAggressiveFolds] =
    createSignal<boolean>(true);
  const [saving, setSaving] = createSignal(false);
  const [formError, setFormError] = createSignal<string | null>(null);
  const [preferencesSaving, setPreferencesSaving] = createSignal(false);
  const [preferencesFormError, setPreferencesFormError] = createSignal<
    string | null
  >(null);
  const { addErrorToast } = useToasts();

  function syncProfileDismiss() {
    if (!open()) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (root?.contains(target)) {
        return;
      }
      stopEditing();
      setOpen(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") {
        return;
      }
      stopEditing();
      setOpen(false);
      trigger?.focus();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);

    onCleanup(() => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    });
  }

  createEffect(syncProfileDismiss);

  function syncPreferencesModalDismiss() {
    if (!preferencesOpen()) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") {
        return;
      }
      closePreferences();
      trigger?.focus();
    };

    document.addEventListener("keydown", handleKeyDown);

    onCleanup(() => {
      document.removeEventListener("keydown", handleKeyDown);
    });
  }

  createEffect(syncPreferencesModalDismiss);

  const profileName = () => {
    const profile = storedProfile();
    if (profile === null) {
      return "___";
    }
    return profile.username;
  };

  const actionLabel = () => {
    const profile = storedProfile();
    if (profile === null) {
      return "Log in";
    }
    return "Edit username";
  };

  const submitLabel = () => {
    const profile = storedProfile();
    if (profile === null) {
      return "Create";
    }
    return "Save";
  };

  function startEditing() {
    const profile = storedProfile();
    if (profile === null) {
      setDraftUsername("");
    } else {
      setDraftUsername(profile.username);
    }
    setFormError(null);
    setEditing(true);
  }

  function stopEditing() {
    setEditing(false);
    setDraftUsername("");
    setFormError(null);
  }

  function openPreferences() {
    const preferences = props.preferences;
    if (preferences !== null) {
      setDraftAggressiveFolds(preferences.aggressive_folds);
    }
    setPreferencesFormError(null);
    setOpen(false);
    setPreferencesOpen(true);
  }

  function closePreferences() {
    setPreferencesOpen(false);
    setPreferencesFormError(null);
  }

  async function persistProfile() {
    if (saving()) {
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const currentProfile = storedProfile();
      let savedProfile: UserProfile;
      if (currentProfile === null) {
        savedProfile = await createUserProfile(draftUsername());
      } else {
        savedProfile = await updateUserProfile(
          currentProfile.id,
          draftUsername(),
        );
      }
      const nextStoredProfile = toStoredProfile(savedProfile);
      saveStoredProfile(nextStoredProfile);
      setStoredProfile(nextStoredProfile);
      stopEditing();
      setOpen(false);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to save profile.";
      setFormError(message);
      addErrorToast("Failed to save profile", error);
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmit(event: SubmitEvent) {
    event.preventDefault();
    await persistProfile();
  }

  async function persistPreferences() {
    if (preferencesSaving()) {
      return;
    }
    setPreferencesSaving(true);
    setPreferencesFormError(null);
    try {
      const preferences = props.preferences;
      if (preferences === null) {
        throw new Error("Preferences are not loaded.");
      }
      const savedPreferences = await updatePreferences(
        preferences.id,
        draftAggressiveFolds(),
      );
      props.onPreferencesSaved(savedPreferences);
      closePreferences();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to save preferences.";
      setPreferencesFormError(message);
      addErrorToast("Failed to save preferences", error);
    } finally {
      setPreferencesSaving(false);
    }
  }

  async function handlePreferencesSubmit(event: SubmitEvent) {
    event.preventDefault();
    await persistPreferences();
  }

  return (
    <div ref={root} class="profile-menu" data-open={open() ? "true" : "false"}>
      <button
        ref={trigger}
        type="button"
        class="profile-trigger"
        aria-haspopup="menu"
        aria-expanded={open() ? "true" : "false"}
        aria-label="Profile"
        title="Profile"
        onClick={() => {
          if (open()) {
            stopEditing();
            setOpen(false);
            return;
          }
          setOpen(true);
        }}
      >
        <CircleUserRound class="profile-trigger-icon" aria-hidden="true" />
      </button>
      <Show when={open()}>
        <div class="profile-popover" role="menu" aria-label="Profile">
          <div class="profile-popover-header">
            <CircleUserRound class="profile-popover-icon" aria-hidden="true" />
            <div class="profile-popover-copy">
              <strong>{profileName()}</strong>
              <span>Profile</span>
            </div>
          </div>
          <div class="profile-popover-divider" />
          <Show
            when={editing()}
            fallback={
              <>
                <button
                  type="button"
                  class="profile-popover-option"
                  role="menuitem"
                  onClick={startEditing}
                >
                  {actionLabel()}
                </button>
                <button
                  type="button"
                  class="profile-popover-option"
                  role="menuitem"
                  onClick={openPreferences}
                >
                  Preferences
                </button>
              </>
            }
          >
            <form class="profile-form" onSubmit={handleSubmit}>
              <label class="profile-form-field">
                <span>Username</span>
                <input
                  type="text"
                  value={draftUsername()}
                  onInput={(event) =>
                    setDraftUsername(event.currentTarget.value)
                  }
                  disabled={saving()}
                />
              </label>
              <Show when={formError() !== null}>
                <pre class="profile-form-error">{formError()}</pre>
              </Show>
              <div class="profile-form-actions">
                <button
                  type="submit"
                  class="profile-form-submit"
                  disabled={saving()}
                >
                  {saving() ? "Saving..." : submitLabel()}
                </button>
                <button
                  type="button"
                  class="profile-form-cancel"
                  disabled={saving()}
                  onClick={stopEditing}
                >
                  Cancel
                </button>
              </div>
            </form>
          </Show>
          <Show when={storedProfile() !== null && !editing()}>
            <button
              type="button"
              class="profile-popover-option"
              role="menuitem"
              onClick={() => {
                clearStoredProfile();
                setStoredProfile(null);
                setOpen(false);
              }}
            >
              Forget local profile
            </button>
          </Show>
        </div>
      </Show>
      <PreferencesModal
        open={preferencesOpen()}
        preferences={props.preferences}
        preferencesPending={props.preferencesPending}
        preferencesError={props.preferencesError}
        draftAggressiveFolds={draftAggressiveFolds()}
        saving={preferencesSaving()}
        formError={preferencesFormError()}
        onAggressiveFoldsChange={setDraftAggressiveFolds}
        onClose={closePreferences}
        onReloadPreferences={props.onReloadPreferences}
        onSubmit={handlePreferencesSubmit}
      />
    </div>
  );
}

function PreferencesModal(props: {
  open: boolean;
  preferences: Preferences | null;
  preferencesPending: boolean;
  preferencesError: string | null;
  draftAggressiveFolds: boolean;
  saving: boolean;
  formError: string | null;
  onAggressiveFoldsChange: (next: boolean) => void;
  onClose: () => void;
  onReloadPreferences: () => Promise<void> | void;
  onSubmit: (event: SubmitEvent) => Promise<void>;
}) {
  return (
    <Show when={props.open}>
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
          <Show when={props.preferencesPending}>
            <p class="profile-preferences-message">Loading preferences...</p>
          </Show>
          <Show when={props.preferencesError !== null}>
            <div class="profile-preferences-error-block">
              <pre class="profile-form-error">{props.preferencesError}</pre>
              <button
                type="button"
                class="profile-form-cancel"
                onClick={() => {
                  void props.onReloadPreferences();
                }}
              >
                Reload
              </button>
            </div>
          </Show>
          <Show
            when={props.preferences !== null}
            fallback={
              <Show
                when={
                  !props.preferencesPending && props.preferencesError === null
                }
              >
                <p class="profile-preferences-message">
                  Preferences are unavailable.
                </p>
              </Show>
            }
          >
            <form class="profile-preferences-form" onSubmit={props.onSubmit}>
              <label class="profile-checkbox-row">
                <span class="profile-checkbox-copy">
                  <strong>Aggressive folds</strong>
                  <span>Collapse unchanged regions when fold hints exist.</span>
                </span>
                <span class="profile-checkbox-control">
                  <input
                    class="profile-checkbox-input"
                    type="checkbox"
                    checked={props.draftAggressiveFolds}
                    disabled={props.saving}
                    onInput={(event) =>
                      props.onAggressiveFoldsChange(event.currentTarget.checked)
                    }
                  />
                  <span
                    class="visibility-indicator large profile-checkbox-indicator"
                    classList={{ visible: props.draftAggressiveFolds }}
                    aria-hidden="true"
                  />
                </span>
              </label>
              <Show when={props.formError !== null}>
                <pre class="profile-form-error">{props.formError}</pre>
              </Show>
              <div class="profile-form-actions profile-preferences-actions">
                <button
                  type="submit"
                  class="profile-form-submit"
                  disabled={props.saving}
                >
                  {props.saving ? "Saving..." : "Save"}
                </button>
                <button
                  type="button"
                  class="profile-form-cancel"
                  disabled={props.saving}
                  onClick={props.onClose}
                >
                  Cancel
                </button>
              </div>
            </form>
          </Show>
        </section>
      </div>
    </Show>
  );
}

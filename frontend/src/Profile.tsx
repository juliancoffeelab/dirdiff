import { Show, createEffect, createSignal, onCleanup } from "solid-js";
import { CircleUserRound } from "lucide-solid";
import { createUserProfile, updateUserProfile, type UserProfile } from "./api";
import { useToasts } from "./Toasts";
import {
  clearStoredProfile,
  loadStoredProfile,
  saveStoredProfile,
  toStoredProfile,
  type StoredProfile,
} from "./storage";

export function Profile() {
  let root: HTMLDivElement | undefined;
  let trigger: HTMLButtonElement | undefined;
  const [open, setOpen] = createSignal(false);
  const [storedProfile, setStoredProfile] = createSignal<StoredProfile | null>(
    loadStoredProfile(),
  );
  const [editing, setEditing] = createSignal(false);
  const [draftUsername, setDraftUsername] = createSignal("");
  const [saving, setSaving] = createSignal(false);
  const [formError, setFormError] = createSignal<string | null>(null);
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
              <button
                type="button"
                class="profile-popover-option"
                role="menuitem"
                onClick={startEditing}
              >
                {actionLabel()}
              </button>
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
    </div>
  );
}

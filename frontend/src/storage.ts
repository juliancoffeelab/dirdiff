import { z } from "zod";
import type { UserProfile } from "./api";

export const PROFILE_STORAGE_KEY = "dirdiff:v1:profile";

export const StoredProfileSchema = z.strictObject({
  id: z.number().int().positive(),
  username: z.string().min(1),
});
export type StoredProfile = z.infer<typeof StoredProfileSchema>;

export function loadStoredProfile(): StoredProfile | null {
  const raw = localStorage.getItem(PROFILE_STORAGE_KEY);
  if (raw === null) {
    return null;
  }
  return StoredProfileSchema.parse(JSON.parse(raw));
}

export function saveStoredProfile(profile: StoredProfile): void {
  localStorage.setItem(
    PROFILE_STORAGE_KEY,
    JSON.stringify(StoredProfileSchema.parse(profile)),
  );
}

export function toStoredProfile(profile: UserProfile): StoredProfile {
  return StoredProfileSchema.parse(profile);
}

export function clearStoredProfile(): void {
  localStorage.removeItem(PROFILE_STORAGE_KEY);
}

/**
 * Loads Vite's ambient browser and asset-module declarations for this frontend.
 *
 * TypeScript includes this file through the frontend project. The reference makes
 * Vite-provided globals and supported asset imports available without creating a
 * runtime module or adding dirdiff application declarations.
 */
/// <reference types="vite/client" />

/**
 * Describes dirdiff's development-only Vite environment input.
 *
 * Vite startup requires the backend origin while serving the editable HUD. The
 * value is absent from release builds, whose HUD shares the backend origin.
 */
interface ImportMetaEnv {
  /** Absolute paired backend origin used for API proxying and onboarding links. */
  readonly VITE_DIRDIFF_BACKEND_ORIGIN?: string;
}

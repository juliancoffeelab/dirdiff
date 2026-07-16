/**
 * Provides the small, domain-independent arithmetic operation shared by the
 * frontend renderer.
 *
 * This module must not depend on the DOM, Solid, diff data, or application
 * state. The exported function validates its numeric contract at runtime so
 * invalid bounds fail at their source instead of corrupting token layout.
 */

/**
 * Restricts a finite number to the inclusive `[min, max]` interval.
 *
 * All three arguments must be finite and `min` must not exceed `max`. Invalid
 * bounds throw immediately rather than silently returning a misleading value.
 */
export function clamp(value: number, min: number, max: number): number {
  if (![value, min, max].every(Number.isFinite)) {
    throw new Error(
      `Clamp arguments must be finite, received ${value}, ${min}, ${max}.`,
    );
  }
  if (min > max) {
    throw new Error(`Clamp minimum ${min} exceeds maximum ${max}.`);
  }
  return Math.min(Math.max(value, min), max);
}

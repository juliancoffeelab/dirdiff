/**
 * Provide small, domain-independent arithmetic operations shared by frontend
 * modules.
 *
 * This module must not depend on the DOM, Solid, diff data, or application
 * state. Its functions validate their numeric contracts at runtime so invalid
 * collection bounds fail at their source instead of producing `NaN` or an
 * out-of-range index elsewhere in the UI.
 */

/**
 * Wrap an integer index into a non-empty collection.
 *
 * Negative and oversized indices are supported. `index` and `length` must be
 * integers, and `length` must be positive; violating those requirements throws
 * before modulo arithmetic can produce an invalid collection index.
 */
export function wrapIndex(index: number, length: number): number {
  if (!Number.isInteger(index)) {
    throw new Error(`Index must be an integer, received ${index}.`);
  }
  if (!Number.isInteger(length) || length <= 0) {
    throw new Error(`Length must be a positive integer, received ${length}.`);
  }
  return ((index % length) + length) % length;
}

/**
 * Restrict a finite number to the inclusive `[min, max]` interval.
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

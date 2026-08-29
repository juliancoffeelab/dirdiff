/**
 * Provides small domain-independent invariant and arithmetic operations shared
 * by frontend modules.
 *
 * This module must not depend on the DOM, Solid, diff data, or application state.
 * Its exported functions validate caller contracts at runtime and return only
 * values justified by those checks; it must not suppress type errors or recover
 * from violated invariants.
 */

/**
 * Rejects one false runtime invariant and narrows values proven by that condition.
 *
 * Callers provide the condition they require and may provide a boundary-specific
 * error message. A false condition always throws; a true condition returns no value.
 *
 * @param condition Runtime invariant that must hold.
 * @param message Error text for this boundary, or `null` for the generic assertion
 * message.
 */
export function assert(
  condition: boolean,
  message: string | null = null,
): asserts condition {
  if (!condition) {
    throw new Error(message ?? "Assertion failed.");
  }
}

/**
 * Returns one present value or rejects a missing-value invariant.
 *
 * Null and undefined are the only missing values. Callers may provide a
 * boundary-specific error message and receive the original value type without a
 * non-null type assumption.
 *
 * @param value Value whose presence the caller requires.
 * @param message Error text for this boundary, or `null` for the generic missing
 * value message.
 */
export function expect<T>(
  value: T | null | undefined,
  message: string | null = null,
): T {
  if (value === null || value === undefined) {
    throw new Error(message ?? `Expected value, got ${String(value)}.`);
  }
  return value;
}

/**
 * Restricts a finite number to the inclusive `[min, max]` interval.
 *
 * All three arguments must be finite and `min` must not exceed `max`. Invalid
 * bounds throw immediately rather than silently returning a misleading value.
 *
 * @param value Number to restrict.
 * @param min Inclusive lower bound.
 * @param max Inclusive upper bound.
 */
export function clamp(value: number, min: number, max: number): number {
  assert(
    [value, min, max].every(Number.isFinite),
    `Clamp arguments must be finite, received ${value}, ${min}, ${max}.`,
  );
  assert(min <= max, `Clamp minimum ${min} exceeds maximum ${max}.`);
  return Math.min(Math.max(value, min), max);
}

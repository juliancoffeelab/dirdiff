export function assert(
  condition: boolean,
  message: string | null = null,
): asserts condition {
  if (!condition) {
    throw new Error(message ?? "Assertion failed.");
  }
}

export function expect<T>(
  value: T | null | undefined,
  message: string | null = null,
): T {
  if (value === null || value === undefined) {
    throw new Error(message ?? `Expected value, got ${String(value)}.`);
  }
  return value;
}

export function clamp(value: number, min: number, max: number): number {
  assert(
    [value, min, max].every(Number.isFinite),
    `Clamp arguments must be finite, received ${value}, ${min}, ${max}.`,
  );
  assert(min <= max, `Clamp minimum ${min} exceeds maximum ${max}.`);
  return Math.min(Math.max(value, min), max);
}

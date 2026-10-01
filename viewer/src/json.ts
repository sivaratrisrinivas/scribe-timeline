/**
 * Reading values out of parsed JSON, or refusing.
 *
 * Shared by both parsers in this viewer. The guards are small, but they are the
 * only thing standing between a corrupt file and a plausible wrong number, and
 * two private copies of them would be two places for that rule to drift.
 *
 * Two conventions run through all of them:
 *
 * - **Every failure names the path.** "must be a number" leaves a reader guessing
 *   which of two hundred fields is wrong; `conditions[0].median_marker_ms` does
 *   not.
 * - **Absence is never silently `undefined`.** A missing number is a missing
 *   number. Where a field is genuinely nullable, the parser asks for nullability
 *   explicitly rather than getting it from `undefined`.
 */

export class InvalidJsonFieldError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "InvalidJsonFieldError";
  }
}

export function requireNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new InvalidJsonFieldError(`${path} must be a finite number, got ${String(value)}`);
  }
  return value;
}

export function requireString(value: unknown, path: string): string {
  if (typeof value !== "string") {
    throw new InvalidJsonFieldError(`${path} must be a string, got ${typeof value}`);
  }
  return value;
}

export function requireBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    throw new InvalidJsonFieldError(`${path} must be a boolean, got ${typeof value}`);
  }
  return value;
}

export function requireObject(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new InvalidJsonFieldError(`${path} must be an object, got ${typeof value}`);
  }
  return value as Record<string, unknown>;
}

export function requireArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new InvalidJsonFieldError(`${path} must be an array, got ${typeof value}`);
  }
  return value;
}

export function requireField(
  object: Record<string, unknown>,
  key: string,
  path: string,
): unknown {
  if (!Object.hasOwn(object, key)) {
    throw new InvalidJsonFieldError(`${path}.${key} is missing; a figure that is not there is not zero`);
  }
  return object[key];
}

export function requireExactKeys(
  object: Record<string, unknown>,
  expected: readonly string[],
  path: string,
): void {
  const unknown = Object.keys(object).filter((key) => !expected.includes(key));
  if (unknown.length > 0) {
    throw new InvalidJsonFieldError(
      `${path} has field(s) this viewer does not know: ${unknown.join(", ")}. ` +
        `They are ignored here, so a figure the report contains would be missing from ` +
        `the page; the viewer is older than the report it was handed.`,
    );
  }
  const missing = expected.filter((key) => !Object.hasOwn(object, key));
  if (missing.length > 0) {
    throw new InvalidJsonFieldError(
      `${path} is missing field(s): ${missing.join(", ")}. A figure that is not there is not zero.`,
    );
  }
}

export function optionalString(value: unknown, path: string): string | null {
  return value === null || value === undefined ? null : requireString(value, path);
}

export function optionalNumber(value: unknown, path: string): number | null {
  return value === null || value === undefined ? null : requireNumber(value, path);
}

/** A number the document must carry, as either a number or an explicit null.
 *
 *  Distinct from `optionalNumber`, which treats an absent key as null. Here the
 *  difference is the whole point: a delta the report left out and a delta the
 *  report reported as null would both render as a dash, and only one of them
 *  means "this is the baseline".
 */
export function nullableNumber(value: unknown, path: string): number | null {
  if (value === null) return null;
  return requireNumber(value, path);
}

export function requirePositiveNumber(value: unknown, path: string): number {
  const parsed = requireNumber(value, path);
  if (parsed <= 0) {
    throw new InvalidJsonFieldError(`${path} must be positive, got ${parsed}`);
  }
  return parsed;
}

export function requireNonNegativeNumber(value: unknown, path: string): number {
  const parsed = requireNumber(value, path);
  if (parsed < 0) {
    throw new InvalidJsonFieldError(`${path} must not be negative, got ${parsed}`);
  }
  return parsed;
}

export function requireNonNegativeInteger(value: unknown, path: string): number {
  const parsed = requireNumber(value, path);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new InvalidJsonFieldError(`${path} must be a non-negative integer, got ${parsed}`);
  }
  return parsed;
}

/** Exactly two numbers, as an ordered pair. */
export function requirePair(value: unknown, path: string): readonly [number, number] {
  const pair = requireArray(value, path);
  if (pair.length !== 2) {
    throw new InvalidJsonFieldError(`${path} must hold exactly two numbers, got ${pair.length}`);
  }
  return [requireNumber(pair[0], `${path}[0]`), requireNumber(pair[1], `${path}[1]`)];
}

/** A map from string keys to finite numbers, such as the reported offsets. */
export function requireNumberMap(
  value: unknown,
  path: string,
): Readonly<Record<string, number>> {
  const entries: Record<string, number> = {};
  for (const [key, raw] of Object.entries(requireObject(value, path))) {
    entries[key] = requireNumber(raw, `${path}.${key}`);
  }
  return entries;
}

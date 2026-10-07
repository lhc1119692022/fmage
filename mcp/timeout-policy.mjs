// Omitted timeouts use the runtime default; explicit values are never raised.
export function timeoutSeconds(...values) {
  for (const value of values) {
    if (value === undefined || value === null) continue;
    if (!Number.isInteger(value) || value <= 0) throw new Error("timeout must be a positive integer.");
    return value;
  }
  return null;
}

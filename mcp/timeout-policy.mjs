export const MIN_TIMEOUT_SECONDS = 600;
export const MIN_TIMEOUT_MILLISECONDS = MIN_TIMEOUT_SECONDS * 1000;

export function timeoutSeconds(...values) {
  return Math.max(
    MIN_TIMEOUT_SECONDS,
    ...values.map(Number).filter((value) => Number.isInteger(value) && value > 0),
  );
}

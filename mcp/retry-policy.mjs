// Configurable generation retry: every retry is a fresh provider request, never
// a poll of the request that already failed.

export const DEFAULT_RETRY_COUNT = 2;
export const MAX_RETRY_COUNT = 10;
const RETRY_DELAY_STEP_MS = 1000;
const TRANSIENT_HTTP_STATUSES = new Set([408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524]);
const TRANSIENT_MESSAGE_PATTERN =
  /timed?\s*out|timeout|network\s*error|fetch failed|socket hang up|econnreset|econnrefused|etimedout|eai_again|temporarily unavailable|service unavailable|overloaded|rate\s*limit|too many requests|concurren(?:cy|t limit)|try again|限流|并发|繁忙|请稍后|稍后重试/iu;

/** Read and validate the two global retry fields in providers.json. */
export function generationRetryPolicy(config) {
  const configured = config?.retry_on_failure;
  if (configured !== undefined && typeof configured !== "boolean") {
    throw new Error("retry_on_failure must be a boolean.");
  }
  const count = config?.retry_count;
  if (
    count !== undefined &&
    (!Number.isInteger(count) || count < 0 || count > MAX_RETRY_COUNT)
  ) {
    throw new Error(`retry_count must be an integer from 0 to ${MAX_RETRY_COUNT}.`);
  }
  return {
    enabled: configured === true,
    retries: configured === true ? count ?? DEFAULT_RETRY_COUNT : 0,
  };
}

function failureMessage(error) {
  return error instanceof Error ? error.message : String(error ?? "");
}

function failureStatus(error) {
  if (Number.isInteger(error?.status) && error.status >= 100 && error.status <= 599) {
    return error.status;
  }
  const match = failureMessage(error).match(/\bHTTP\s+(\d{3})\b/iu);
  return match ? Number(match[1]) : null;
}

/**
 * A retry must be a new submission. Failures that already own a remote task or
 * that ended with an unknown submission state are not retried, because another
 * submission could duplicate a paid generation.
 */
export function isRetryableGenerationFailure(error) {
  if (!error) return false;
  if (error.stage === "submission_uncertain") return false;
  if (error.remote_task_id || error.remote_status) return false;
  const status = failureStatus(error);
  if (status !== null) return TRANSIENT_HTTP_STATUSES.has(status);
  return TRANSIENT_MESSAGE_PATTERN.test(failureMessage(error));
}

/** Short pause before a fresh attempt; grows linearly to give a busy slot time to free. */
export function retryDelayMs(attempt) {
  return RETRY_DELAY_STEP_MS * Math.max(1, attempt);
}

/**
 * Run `submit` up to 1 + retries times. `submit` must perform the whole provider
 * request again; this helper never re-reads a previous result.
 */
export async function withGenerationRetry(
  submit,
  { retries = 0, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) } = {},
) {
  let attempts = 0;
  for (;;) {
    attempts += 1;
    try {
      return { result: await submit(), attempts };
    } catch (error) {
      const canRetry =
        attempts <= retries && isRetryableGenerationFailure(error);
      if (!canRetry) {
        if (attempts > 1 && error instanceof Error) {
          error.message = `${error.message} (generation request failed after ${attempts} attempts.)`;
          error.generation_attempts = attempts;
        }
        throw error;
      }
      await sleep(retryDelayMs(attempts));
    }
  }
}

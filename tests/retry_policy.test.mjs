import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_RETRY_COUNT, generationRetryPolicy, isRetryableGenerationFailure,
  retryDelayMs, withGenerationRetry,
} from '../mcp/retry-policy.mjs';

test('retry policy is opt-in and validates its two configuration fields', () => {
  assert.deepEqual(generationRetryPolicy({}), { enabled: false, retries: 0 });
  assert.deepEqual(generationRetryPolicy({ retry_on_failure: false, retry_count: 5 }), { enabled: false, retries: 0 });
  assert.deepEqual(generationRetryPolicy({ retry_on_failure: true }), { enabled: true, retries: DEFAULT_RETRY_COUNT });
  assert.deepEqual(generationRetryPolicy({ retry_on_failure: true, retry_count: 0 }), { enabled: true, retries: 0 });
  assert.throws(() => generationRetryPolicy({ retry_on_failure: 'yes' }), /must be a boolean/);
  assert.throws(() => generationRetryPolicy({ retry_on_failure: true, retry_count: 1.5 }), /integer/);
  assert.throws(() => generationRetryPolicy({ retry_on_failure: true, retry_count: 11 }), /integer/);
});

test('only transient submission failures are retryable', () => {
  assert.equal(isRetryableGenerationFailure(new Error('HTTP 429: rate limited')), true);
  assert.equal(isRetryableGenerationFailure(new Error('HTTP 503: busy')), true);
  assert.equal(isRetryableGenerationFailure(new Error('Network error: ECONNRESET')), true);
  assert.equal(isRetryableGenerationFailure(new Error('Fmage helper timed out after 900 seconds.')), true);
  assert.equal(isRetryableGenerationFailure(new Error('HTTP 400: invalid prompt')), false);
  assert.equal(isRetryableGenerationFailure(new Error('HTTP 401: bad key')), false);
  assert.equal(isRetryableGenerationFailure(new Error('Missing provider API key.')), false);
  assert.equal(isRetryableGenerationFailure(Object.assign(new Error('HTTP 429'), { stage: 'submission_uncertain' })), false);
  assert.equal(isRetryableGenerationFailure(Object.assign(new Error('HTTP 429'), { remote_task_id: 'task-1' })), false);
});

test('a retry performs another submission and preserves the final failure', async () => {
  const calls = [];
  const sleeps = [];
  const attempts = [new Error('HTTP 429: busy'), new Error('HTTP 502: upstream'), { images: ['out.png'] }];
  const result = await withGenerationRetry(async () => {
    calls.push(calls.length + 1);
    const next = attempts.shift();
    if (next instanceof Error) throw next;
    return next;
  }, { retries: 2, sleep: async ms => { sleeps.push(ms); } });
  assert.deepEqual(calls, [1, 2, 3]);
  assert.deepEqual(sleeps, [retryDelayMs(1), retryDelayMs(2)]);
  assert.deepEqual(result, { result: { images: ['out.png'] }, attempts: 3 });

  const failures = [];
  await assert.rejects(
    withGenerationRetry(async () => {
      failures.push(failures.length + 1);
      throw new Error('HTTP 429: busy');
    }, { retries: 1, sleep: async () => {} }),
    error => /after 2 attempts/.test(error.message) && error.generation_attempts === 2,
  );
  assert.deepEqual(failures, [1, 2]);
});

test('a permanent failure is not submitted twice', async () => {
  let calls = 0;
  await assert.rejects(
    withGenerationRetry(async () => {
      calls += 1;
      throw new Error('HTTP 400: invalid prompt');
    }, { retries: 3, sleep: async () => {} }),
    /invalid prompt/,
  );
  assert.equal(calls, 1);
});

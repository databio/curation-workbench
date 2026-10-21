// The app's one resource cache.
import { CurationError } from '../api/backend';
import { createResourceCache, type RetryPolicy } from '../services/resourceCache';

/** Retry network failures and 5xx twice; never a 4xx (it will not change by asking again). */
export const appRetry: RetryPolicy = (failureCount, error) =>
  failureCount < 2 && !(error instanceof CurationError && error.status < 500);

export const createAppCache = () => createResourceCache({ staleTime: 15_000, retry: appRetry });

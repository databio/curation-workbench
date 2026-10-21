/**
 * Standalone auth utilities — usable without the React context.
 *
 * These functions extract the core logic from context.tsx so consumers that
 * do not want the full SnapgateProvider can still use the same battle-tested
 * patterns for refresh scheduling and error classification.
 */

import { AuthError } from './types';

// ---------------------------------------------------------------------------
// Token refresh scheduling
// ---------------------------------------------------------------------------

/**
 * Schedule a callback to fire 1 minute before the token expires.
 *
 * If the token is already within 1 minute of expiry (or already expired),
 * the callback fires immediately.
 *
 * Returns a cancel function. Call it before scheduling the next refresh to
 * avoid overlapping timers.
 *
 * @param expiresAt  ISO 8601 timestamp of when the access token expires
 *                   (the `access_expires_at` field from the API).
 * @param onRefresh  Async callback that performs the refresh. Called with no
 *                   arguments. If it throws, the error is passed to
 *                   `onError` (if provided) and the timer is not rescheduled.
 * @param onError    Optional error handler. Receives the error thrown by
 *                   `onRefresh`. Defaults to `console.error`.
 *
 * @example
 * const cancel = scheduleTokenRefresh(response.access_expires_at, async () => {
 *   const newTokens = await myRefreshFunction();
 *   cancel();
 *   scheduleTokenRefresh(newTokens.access_expires_at, ...);
 * });
 */
export function scheduleTokenRefresh(
  expiresAt: string,
  onRefresh: () => Promise<void>,
  onError?: (error: unknown) => void,
): () => void {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;

  const handleError = onError ?? ((err: unknown) => console.error('Token refresh failed:', err));

  try {
    const expiryTime = new Date(expiresAt).getTime();
    const now = Date.now();
    const timeUntilExpiry = expiryTime - now;
    // Fire 1 minute early; if already within 1 minute, fire immediately
    const delay = Math.max(timeUntilExpiry - 60_000, 0);

    if (delay > 0) {
      timeoutId = setTimeout(() => {
        onRefresh().catch(handleError);
      }, delay);
    } else {
      // Fire asynchronously so the caller can assign the cancel function first
      timeoutId = setTimeout(() => {
        onRefresh().catch(handleError);
      }, 0);
    }
  } catch (e) {
    // If the date string is unparseable, fall back to 10-minute interval
    console.error('scheduleTokenRefresh: could not parse expiresAt, falling back to 10-minute refresh', e);
    timeoutId = setTimeout(() => {
      onRefresh().catch(handleError);
    }, 10 * 60 * 1000);
  }

  return () => {
    if (timeoutId !== undefined) {
      clearTimeout(timeoutId);
    }
  };
}

// ---------------------------------------------------------------------------
// Error classification
// ---------------------------------------------------------------------------

/**
 * Normalise any thrown value into a typed `AuthError`.
 *
 * Values that already look like `AuthError` (have `code` and `message` string
 * fields) are returned as-is. Everything else is wrapped with a generic
 * `unknown_error` code.
 */
export function classifyAuthError(error: unknown): AuthError {
  if (
    error !== null &&
    typeof error === 'object' &&
    'code' in error &&
    'message' in error &&
    typeof (error as AuthError).code === 'string' &&
    typeof (error as AuthError).message === 'string'
  ) {
    return error as AuthError;
  }

  if (error instanceof Error) {
    return {
      code: 'unknown_error',
      message: error.message,
    };
  }

  return {
    code: 'unknown_error',
    message: String(error),
  };
}

/**
 * Turn a failed response into an `AuthError`, reading the code and message
 * from whichever body shape the server sent:
 *
 * - snapgate `AuthError` (FastAPI HTTPException):
 *   `{"detail": {"error": "rate_limited", "message": "..."}}`
 * - plain HTTPException: `{"detail": "Invalid code"}`
 * - RFC 6749 (`/oauth/token`, `/oauth/register`):
 *   `{"error": "invalid_grant", "error_description": "..."}`
 * - flat `{"error": ..., "message": ...}`
 *
 * `detail.field` (sent with `invalid_input`) is copied onto the error.
 *
 * A non-JSON body gives `network_error`; unparseable JSON gives `parse_error`.
 */
export async function readAuthError(response: Response): Promise<AuthError> {
  const status = response.status;
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    return {
      code: 'network_error',
      message: `Server returned ${status}: ${response.statusText}`,
      status,
    };
  }

  let data: unknown;
  try {
    data = await response.json();
  } catch {
    return { code: 'parse_error', message: 'Failed to parse error response', status };
  }

  const str = (v: unknown): string | undefined =>
    typeof v === 'string' && v ? v : undefined;
  const obj = (v: unknown): Record<string, unknown> =>
    v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

  const body = obj(data);
  const detail = obj(body.detail);
  const error: AuthError = {
    code: str(detail.error) || str(body.error) || 'unknown_error',
    message:
      str(detail.message) ||
      str(body.detail) ||
      str(body.message) ||
      str(body.error_description) ||
      'An error occurred',
    status,
  };
  const field = str(detail.field);
  if (field) error.field = field;
  return error;
}

/**
 * Return true if the error represents a network-level failure (no HTTP status,
 * or an explicit `network_error` code) rather than an auth-level failure.
 *
 * Use this to distinguish "the server is unreachable" from "the token is
 * invalid" so you can show an offline banner instead of redirecting to login.
 *
 * @example
 * try {
 *   await api.getSomething();
 * } catch (err) {
 *   const authErr = classifyAuthError(err);
 *   if (isNetworkError(authErr)) {
 *     showOfflineBanner();
 *   } else {
 *     redirectToLogin();
 *   }
 * }
 */
export function isNetworkError(error: AuthError): boolean {
  return error.code === 'network_error' || error.status === undefined;
}

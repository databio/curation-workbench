/**
 * Single-flight token refresh, shared by the SnapgateProvider's scheduled
 * refresh and by API clients that hit a 401 (e.g. after the phone suspended
 * the tab so the scheduled refresh timer never fired).
 *
 * Snapgate rotates refresh tokens on every use: once a token is used, the
 * server refuses it. Every open tab reads the same token from localStorage,
 * so two tabs (or a tab's timer and its 401 handler) that send it at the same
 * moment would see one succeed and the other fail, and the losing tab would
 * fall back to the login page. So a refresh:
 *
 * - is single-flight inside one tab (one in-flight promise),
 * - holds a cross-tab Web Lock (`navigator.locks`) while it runs, and reads
 *   the refresh token only once it holds the lock, so a second tab waits and
 *   then uses the token the first tab just stored,
 * - and, when the server refuses the token, checks whether another tab
 *   stored a newer one meanwhile (a browser without Web Locks, or an older
 *   tab) and retries once with it before calling the session dead.
 */
import { RefreshResponse, AuthError } from './types';
import { ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, ACCESS_EXPIRES_AT_KEY } from './constants';
import { readAuthError } from './utils';

/** Name of the cross-tab Web Lock held while a refresh runs. */
export const REFRESH_LOCK_NAME = 'snapgate_refresh';

/**
 * Set when the server refused the stored refresh token (the session ended by
 * itself, not by a sign-out). A login page can read it with
 * `sessionWasLost()` to show "session expired, sign in" instead of sending
 * the person straight to the sign-in issuer. Cleared when new tokens are stored.
 */
export const SESSION_LOST_KEY = 'snapgate_session_lost';

let inFlight: Promise<RefreshResponse | null> | null = null;

/**
 * Refresh the access token using the stored refresh token, persisting the
 * rotated tokens to localStorage.
 *
 * Returns the refresh response, or null when no refresh token is stored
 * (signed out). Throws an AuthError when the server rejects the refresh
 * token (401/403 → session truly dead), sends a coded error such as
 * `rate_limited`, or is unreachable/erroring (network_error → transient,
 * tokens are kept).
 */
export function refreshAccessToken(apiUrl: string): Promise<RefreshResponse | null> {
  if (!inFlight) {
    inFlight = withRefreshLock(() => doRefresh(apiUrl)).finally(() => {
      inFlight = null;
    });
  }
  return inFlight;
}

/** True when the last session ended because its refresh token was refused. */
export function sessionWasLost(): boolean {
  try {
    return localStorage.getItem(SESSION_LOST_KEY) !== null;
  } catch {
    return false;
  }
}

/** Forget the "session lost" mark (called whenever new tokens are stored). */
export function clearSessionLost(): void {
  try {
    localStorage.removeItem(SESSION_LOST_KEY);
  } catch {
    // storage unavailable: nothing to clear
  }
}

/** Record that the session ended by itself (see `SESSION_LOST_KEY`). */
export function markSessionLost(): void {
  try {
    localStorage.setItem(SESSION_LOST_KEY, new Date().toISOString());
  } catch {
    // storage unavailable: the login page just auto-redirects as before
  }
}

function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
  if (!locks || typeof locks.request !== 'function') return fn();
  return locks.request(REFRESH_LOCK_NAME, { mode: 'exclusive' }, fn) as Promise<T>;
}

/** Give up on a refresh request after this long (the lock is held meanwhile). */
const REFRESH_TIMEOUT_MS = 20_000;

function refreshTimeout(): AbortSignal | undefined {
  return typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
    ? AbortSignal.timeout(REFRESH_TIMEOUT_MS)
    : undefined;
}

function storeRefreshed(data: RefreshResponse): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
  localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token);
  if (data.access_expires_at) {
    localStorage.setItem(ACCESS_EXPIRES_AT_KEY, data.access_expires_at);
  }
  clearSessionLost();
}

async function doRefresh(apiUrl: string): Promise<RefreshResponse | null> {
  // Read inside the lock: another tab may have rotated the token while we
  // waited for it.
  let refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refreshToken) return null;

  // Two attempts at most: the second only when another tab stored a newer
  // refresh token while ours was being refused.
  for (let attempt = 0; ; attempt++) {
    const response = await fetch(`${apiUrl}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
      // The cross-tab lock is held for the whole request; never let a hung
      // connection block every tab's refresh.
      signal: refreshTimeout(),
    }).catch((err) => {
      throw {
        code: 'network_error',
        message: `Token refresh failed: ${err instanceof Error ? err.message : String(err)}`,
      } as AuthError;
    });

    if (response.ok) {
      const data = (await response.json()) as RefreshResponse;
      storeRefreshed(data);
      return data;
    }

    if (response.status === 401 || response.status === 403) {
      const latest = localStorage.getItem(REFRESH_TOKEN_KEY);
      if (attempt === 0 && latest && latest !== refreshToken) {
        // Another tab won the race and stored the successor. Use it.
        refreshToken = latest;
        continue;
      }
      markSessionLost();
      throw {
        code: 'token_rotation_failed',
        message: 'Refresh token rejected - session expired',
        status: response.status,
      } as AuthError;
    }

    // A coded server error (e.g. 429 rate_limited) passes through as-is.
    const serverError = await readAuthError(response);
    if (!['unknown_error', 'parse_error', 'network_error'].includes(serverError.code)) {
      throw serverError;
    }
    // 5xx / anything else: transient server trouble. Keep the stored tokens
    // so a later attempt can still succeed.
    throw {
      code: 'network_error',
      message: `Token refresh failed: server returned ${response.status}`,
      status: response.status,
    } as AuthError;
  }
}

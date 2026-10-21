import React, { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { AuthContextValue, AuthState, AuthError, RememberedAccount, User } from './types';
import { AuthAPI } from './api';
import { refreshAccessToken, clearSessionLost } from './refreshClient';
import { scheduleTokenRefresh } from './utils';
import { ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, ACCESS_EXPIRES_AT_KEY } from './constants';
import {
  getRememberedAccounts,
  addRememberedAccount,
  removeRememberedAccount as removeFromStorage,
} from './rememberedAccounts';

/** The provider's context. For tests and harnesses; apps use SnapgateProvider. */
export const SnapgateContext = createContext<AuthContextValue | null>(null);

function getStoredTokens() {
  return {
    accessToken: localStorage.getItem(ACCESS_TOKEN_KEY),
    refreshToken: localStorage.getItem(REFRESH_TOKEN_KEY),
  };
}

function storeTokens(accessToken: string, refreshToken: string, expiresAt?: string | null) {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  if (expiresAt) {
    localStorage.setItem(ACCESS_EXPIRES_AT_KEY, expiresAt);
  }
  clearSessionLost();
}

// Refresh when the access token has less than this left. Larger than the
// 1-minute lead scheduleTokenRefresh uses, so a timer that fires on time
// always refreshes.
const REFRESH_MARGIN_MS = 90_000;
// After a transient refresh failure (offline, 5xx, rate limited), try again
// this soon instead of giving up on the session.
const RETRY_AFTER_TRANSIENT_MS = 30_000;

/** The access token expiry stored by whichever tab refreshed last. */
function storedExpiryMs(): number | null {
  const stored = localStorage.getItem(ACCESS_EXPIRES_AT_KEY);
  if (!stored) return null;
  const ms = new Date(stored).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function clearStoredTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(ACCESS_EXPIRES_AT_KEY);
}

// Module-level shared initialization. Both invocations of the effect under
// React 18 StrictMode share this single promise, so the OAuth code exchange
// happens once and both runs observe the same result. This avoids a race
// where the second run calls /auth/me before the first run has stored tokens.
// See docs/postmortem-strictmode-auth-race.md.
type InitResult = { user: User | null; expiresAt: string | null };
let initPromise: Promise<InitResult> | null = null;

interface SnapgateProviderProps {
  children: React.ReactNode;
  apiUrl: string;
  enableDemoLogin?: boolean;
  onError?: (error: AuthError) => void;
  /**
   * Turn on snapgate's user-admin module (role badges from /auth/roles).
   * Needs the backend's create_useradmin_router. Default false: no extra requests.
   */
  userAdmin?: boolean;
}

export function SnapgateProvider({
  children,
  apiUrl,
  enableDemoLogin = false,
  onError,
  userAdmin = false,
}: SnapgateProviderProps) {
  const [state, setState] = useState<AuthState>({
    user: null,
    loading: true,
    error: null,
  });
  const [rememberedAccounts, setRememberedAccounts] = useState<RememberedAccount[]>([]);
  const [providerLabel, setProviderLabel] = useState<string | null>(null);

  // Memoize API instance to prevent re-creation on every render
  const api = useMemo(() => new AuthAPI(apiUrl), [apiUrl]);

  useEffect(() => {
    let mounted = true;
    api
      .getProvider()
      .then(info => mounted && setProviderLabel(info.label))
      .catch(() => {}); // the button falls back to plain "Sign in"
    return () => {
      mounted = false;
    };
  }, [api]);
  const cancelRefreshRef = useRef<(() => void) | undefined>(undefined);

  // Handle errors consistently
  const handleError = useCallback((error: AuthError) => {
    setState(prev => ({ ...prev, error, loading: false }));

    if (onError) {
      onError(error);
    }

    switch (error.code) {
      case 'rate_limited':
        console.warn('Rate limit hit - backing off');
        break;
      case 'token_rotation_failed':
        console.warn('Token rotation failed - clearing auth');
        setState({ user: null, loading: false, error });
        break;
      case 'user_not_approved':
        console.info('User pending approval');
        break;
      default:
        console.error('Auth error:', error);
    }
  }, [onError]);

  // Perform token refresh via the shared single-flight helper (also used by
  // API clients on 401), which persists the rotated tokens itself.
  const doRefresh = useCallback(async () => {
    const response = await refreshAccessToken(apiUrl);
    if (!response) {
      setState({ user: null, loading: false, error: null });
      return null;
    }
    return response;
  }, [apiUrl]);

  // Refresh now unless the stored access token still has time left (another
  // tab may have refreshed it already); either way, schedule the next one.
  const refreshIfDue = useCallback(async (): Promise<string | null> => {
    const expiry = storedExpiryMs();
    if (expiry !== null && expiry - Date.now() > REFRESH_MARGIN_MS) {
      return new Date(expiry).toISOString();
    }
    const response = await doRefresh();
    if (!response) return null;
    setState(prev => ({ ...prev, user: response.user }));
    return response.access_expires_at;
  }, [doRefresh]);

  // Schedule token refresh based on expiry
  const scheduleRefresh = useCallback((expiresAt: string) => {
    if (cancelRefreshRef.current) {
      cancelRefreshRef.current();
    }
    cancelRefreshRef.current = scheduleTokenRefresh(
      expiresAt,
      async () => {
        const next = await refreshIfDue();
        if (next) scheduleRefresh(next);
      },
      (error) => {
        const authError = error as AuthError;
        if (authError?.code === 'token_rotation_failed') {
          handleError(authError);
          return;
        }
        // Offline, server trouble or rate limited: the session may well be
        // fine. Keep the user signed in and try again shortly.
        console.warn('Token refresh failed, retrying soon:', authError);
        scheduleRefresh(
          new Date(Date.now() + 60_000 + RETRY_AFTER_TRANSIENT_MS).toISOString(),
        );
      },
    );
  }, [refreshIfDue, handleError]);

  // Timers in hidden or sleeping tabs fire late or not at all, so the access
  // token can expire while the tab is idle. Catch up as soon as the tab is
  // looked at again or the network comes back.
  const signedIn = state.user !== null;
  useEffect(() => {
    if (!signedIn) return;
    const wake = () => {
      if (document.visibilityState === 'hidden') return;
      // Fires at once; refreshIfDue refreshes only when the stored token is
      // near expiry, and re-arms the timer from the stored expiry otherwise
      // (another tab may have rotated it).
      scheduleRefresh(new Date().toISOString());
    };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('focus', wake);
    window.addEventListener('online', wake);
    window.addEventListener('pageshow', wake);
    return () => {
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('focus', wake);
      window.removeEventListener('online', wake);
      window.removeEventListener('pageshow', wake);
    };
  }, [signedIn, scheduleRefresh]);

  // Load remembered accounts on mount
  useEffect(() => {
    setRememberedAccounts(getRememberedAccounts());
  }, []);

  // Handle OAuth callback and fetch current user on mount.
  // Uses a module-level shared promise so React StrictMode's double-invoke
  // does not race the OAuth code exchange against /auth/me.
  useEffect(() => {
    let mounted = true;

    if (!initPromise) {
      initPromise = (async (): Promise<InitResult> => {
        const params = new URLSearchParams(window.location.search);
        const code = params.get('code');

        if (code) {
          const cleanUrl = window.location.pathname + window.location.hash;
          window.history.replaceState({}, '', cleanUrl);
          try {
            const response = await api.exchangeCode(code);
            storeTokens(response.access_token, response.refresh_token, response.access_expires_at);
            return { user: response.user, expiresAt: response.access_expires_at };
          } catch {
            // Code may already be consumed (e.g. by a prior StrictMode
            // invocation in another module instance). Fall through to use
            // stored tokens.
          }
        }

        // /auth/me returns null once the access token has expired. If a
        // refresh token is still stored the session is alive, so revive it
        // here rather than booting the user out. refreshAccessToken is
        // single-flight, so this cannot race the scheduled refresh into a
        // double rotation.
        let user = await api.getCurrentUser();
        let expiresAt = localStorage.getItem(ACCESS_EXPIRES_AT_KEY);
        if (!user && localStorage.getItem(REFRESH_TOKEN_KEY)) {
          try {
            const refreshed = await doRefresh();
            if (refreshed) {
              user = refreshed.user;
              expiresAt = refreshed.access_expires_at;
            }
          } catch {
            // refresh token expired/invalid - fall through to logged-out state
          }
        }
        return { user, expiresAt };
      })();
    }

    initPromise
      .then(({ user, expiresAt }) => {
        if (!mounted) return;
        setState({ user, loading: false, error: null });
        if (user) {
          addRememberedAccount(user);
          setRememberedAccounts(getRememberedAccounts());
          if (expiresAt) {
            scheduleRefresh(expiresAt);
          } else {
            const futureExpiry = new Date();
            futureExpiry.setMinutes(futureExpiry.getMinutes() + 10);
            scheduleRefresh(futureExpiry.toISOString());
          }
        }
      })
      .catch((error) => {
        if (mounted) handleError(error as AuthError);
      });

    return () => {
      mounted = false;
      if (cancelRefreshRef.current) {
        cancelRefreshRef.current();
      }
    };
  }, [api, doRefresh, handleError, scheduleRefresh]);

  const login = (loginHint?: string, returnTo?: string) => {
    const params = new URLSearchParams();
    if (loginHint) params.set('login_hint', loginHint);
    if (returnTo) params.set('return_to', returnTo);
    const query = params.toString();
    window.location.href = `${apiUrl}/auth/login${query ? '?' + query : ''}`;
  };

  const removeRememberedAccountHandler = (googleId: string) => {
    removeFromStorage(googleId);
    setRememberedAccounts(getRememberedAccounts());
  };

  const logout = async () => {
    try {
      const { refreshToken } = getStoredTokens();
      if (refreshToken) {
        await api.logout(refreshToken);
      }
      if (cancelRefreshRef.current) {
        cancelRefreshRef.current();
      }
      clearStoredTokens();
      setState({ user: null, loading: false, error: null });
    } catch (error) {
      handleError(error as AuthError);
    }
  };

  const refreshUser = async () => {
    setState(prev => ({ ...prev, loading: true }));
    try {
      const user = await api.getCurrentUser();
      setState({ user, loading: false, error: null });

      if (user) {
        const response = await doRefresh();
        if (response) {
          scheduleRefresh(response.access_expires_at);
        }
      }
    } catch (error) {
      handleError(error as AuthError);
    }
  };

  const demoLogin = enableDemoLogin
    ? async (role: 'admin' | 'user') => {
        setState(prev => ({ ...prev, loading: true }));
        try {
          const response = await api.demoLogin(role);
          storeTokens(response.access_token, response.refresh_token, response.access_expires_at);
          setState({ user: response.user, loading: false, error: null });
          scheduleRefresh(response.access_expires_at);
        } catch (error) {
          handleError(error as AuthError);
        }
      }
    : undefined;

  const value: AuthContextValue = {
    ...state,
    apiUrl,
    login,
    logout,
    refreshUser,
    demoLogin,
    rememberedAccounts,
    removeRememberedAccount: removeRememberedAccountHandler,
    providerLabel,
    userAdmin,
  };

  return <SnapgateContext.Provider value={value}>{children}</SnapgateContext.Provider>;
}

export function useSnapgate() {
  const context = useContext(SnapgateContext);
  if (!context) {
    throw new Error('useSnapgate must be used within SnapgateProvider');
  }
  return context;
}

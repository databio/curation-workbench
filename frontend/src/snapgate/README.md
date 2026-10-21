# Snapgate Frontend Reference Implementation

## ⚠️ DO NOT ROLL YOUR OWN — USE THIS

This directory is the **reference implementation** for Snapgate's React frontend auth.
Copy it into your project as-is. It contains battle-tested patterns that are not
obvious to re-implement correctly.

## What's in This Directory

| File | What it provides |
|------|-----------------|
| `context.tsx` | `SnapgateProvider` — React context with automatic token refresh, StrictMode-safe OAuth code exchange, remembered accounts |
| `api.ts` | `AuthAPI` — Fetch wrapper with error classification and `/auth/me` shape validation; `snapgateFetch` / `readJson` for your own API client (bearer header, refresh-on-401) |
| `types.ts` | TypeScript types: `User`, `AuthError`, `AuthContextValue`, `RefreshResponse`, etc. |
| `index.ts` | Public exports — import from here, not from individual files |
| `utils.ts` | Standalone utilities: `scheduleTokenRefresh`, `classifyAuthError`, `isNetworkError` — usable without React context |
| `rememberedAccounts.ts` | localStorage helpers for remembered accounts feature |
| `refreshClient.ts` | `refreshAccessToken` — single-flight token refresh, shared with your API client |
| `components/` | Ready-to-use UI components: `LoginButton`, `UserMenu`, `AccountSwitcher`, `ProtectedRoute`, `PendingApproval`, `ConnectionsPanel` |
| `admin/` | Optional user admin: `RoleBadge`, `useSiteRole`, `AdminOnlyRoute`, `AdminUsersPage`, `AdminRoleRequestsPage`, `AdminLoginsPage`, `RoleRequestForm`, `UserAdminAPI`. Opt in with `<SnapgateProvider userAdmin>`; see [USAGE.md](./USAGE.md#user-admin-optional-module) |
| `snapgate.css` | The components' own stylesheet — import it once; see [Styling](#styling) |

## How to Copy into Your Project

```bash
cp -r snapgate/frontend/src/snapgate your-project/src/snapgate
npm install react react-router-dom
```

Import the stylesheet once, from wherever your app collects its CSS:

```css
/* src/styles/main.css */
@import '../snapgate/snapgate.css';
```

Then import the components:

```tsx
import { SnapgateProvider, useSnapgate, LoginButton, UserMenu, ProtectedRoute } from './snapgate';
```

That is the whole integration. The package brings its own styling and asks
nothing of yours.

## Styling

`snapgate.css` is self-contained:

- **Every class is prefixed `sg-`**, so nothing can collide with your own
  classes and nothing of yours is required. The package never reaches for a
  host utility class, button, or card.
- **No inline styles, no `!important`, no Tailwind, no Bootstrap.**
- **Every value reads a `--sg-*` custom property with a built-in fallback**, so
  the components look right in a bare app and adopt your palette the moment you
  define those properties.

To make the auth UI match your app, map the `--sg-*` properties onto your own
tokens — usually a short block in your tokens file:

```css
:root {
  --sg-color-text: rgb(var(--color-text));
  --sg-color-muted: rgb(var(--color-text-muted));
  --sg-color-surface: rgb(var(--color-background));
  --sg-color-border: rgb(var(--color-border));
  --sg-color-primary: rgb(var(--color-primary));
  --sg-radius: var(--radius-lg);
}
```

The complete list of overridable properties, with defaults, is in
[USAGE.md](./USAGE.md#theming-the-components) and in the header block of
`snapgate.css` itself.

## Critical Patterns (Why You Must Not Re-implement)

### 1. Proactive Token Refresh via `scheduleRefresh()`

The context schedules a refresh **1 minute before expiry**, not after. This means
users never see a failed request due to an expired token.

```ts
const refreshIn = Math.max(timeUntilExpiry - 60000, 0);
```

A naive implementation that refreshes on 401 errors creates a burst of failed
requests and a worse user experience. Use `scheduleRefresh` from `utils.ts` if
you need the scheduling logic without the React context.

Hidden or sleeping tabs fire timers late, so the context also checks the token
when the tab becomes visible, gains focus, or comes back online, and refreshes
then if it has under 90 seconds left. A refresh that fails for a transient
reason (offline, 5xx, rate limited) is retried in 30 seconds; only a refused
refresh token (`token_rotation_failed`) signs the person out.

### 1b. One Refresh at a Time, Across Tabs

Refresh tokens are single use, and every tab shares the one in localStorage.
`refreshAccessToken` holds a Web Lock (`navigator.locks`, name
`snapgate_refresh`) while it runs and reads the token only once it holds the
lock, so a second tab waits and uses the token the first one stored. If the
server still refuses the token (a browser without Web Locks, or a tab running
older code), it retries once when a newer token has appeared in storage.

When a refresh token is refused for good, `refreshAccessToken` sets a mark that
`sessionWasLost()` reads. A login page that normally sends people straight to
the sign-in issuer should check it and show a "session expired, sign in" button instead.
Storing new tokens clears it.

### 2. StrictMode-Safe Code Exchange

React 18 StrictMode double-invokes effects in development. Without the module-level
shared promise pattern used here, the OAuth code exchange races against `/auth/me`,
leaving users on the login page with valid tokens in localStorage.

The fix: a module-level `Promise<InitResult>` shared across both StrictMode
invocations so the code exchange happens exactly once. See
`docs/postmortem-strictmode-auth-race.md` for the full incident report.

```ts
let initPromise: Promise<InitResult> | null = null;

useEffect(() => {
  let mounted = true;
  if (!initPromise) {
    initPromise = (async () => { /* exchange code once */ })();
  }
  initPromise.then(result => { if (mounted) setState(...); });
  return () => { mounted = false; };
}, [...]);
```

### 3. Error Classification

`api.ts` distinguishes network errors (server unreachable) from auth failures
(401/403), parse errors, and contract violations. The `handleResponse` method
converts raw fetch responses to typed `AuthError` objects with a `code` field.

`utils.ts` exposes `classifyAuthError` and `isNetworkError` as standalone functions
for consumers that need to handle errors outside the context.

### 4. `/auth/me` Response Shape Validation

`AuthAPI.getCurrentUser()` validates that the response contains a `user` key. If
your backend returns the user fields flat (no wrapper), it throws a loud
diagnostic error naming the keys it saw, not a silent "logged out" state.

This catches the most common backend integration mistake immediately.

### 5. Remembered Accounts

The context stores `RememberedAccount` entries in localStorage so users can
quickly re-authenticate by clicking their account name rather than going through
the full OAuth flow with account selection.

## Basic Integration

```tsx
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { SnapgateProvider, useSnapgate, LoginButton, UserMenu, ProtectedRoute } from './snapgate';

function App() {
  return (
    <BrowserRouter>
      <SnapgateProvider apiUrl="http://localhost:8000">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/dashboard" element={
            <ProtectedRoute requireApproval><Dashboard /></ProtectedRoute>
          } />
        </Routes>
      </SnapgateProvider>
    </BrowserRouter>
  );
}

function LoginPage() {
  const { user } = useSnapgate();
  if (user) return <Navigate to="/dashboard" />;
  return <LoginButton />;
}

function Dashboard() {
  const { user } = useSnapgate();
  return <div><UserMenu /><h1>Welcome {user.name}!</h1></div>;
}
```

## Standalone Utilities (No React Required)

For consumers that want only the scheduling or error classification logic without
importing the full React context:

```ts
import { scheduleTokenRefresh, classifyAuthError, isNetworkError } from './snapgate/utils';

// Schedule a refresh callback 1 minute before token expiry
const cancel = scheduleTokenRefresh(response.access_expires_at, async () => {
  const newTokens = await refreshMyTokens();
  cancel(); // cancel old schedule
  scheduleTokenRefresh(newTokens.access_expires_at, ...);
});

// Classify an error caught from a fetch call
const err = classifyAuthError(caught);
if (isNetworkError(err)) {
  showOfflineBanner();
} else {
  redirectToLogin();
}
```

## Updating the Reference

This is a drop-in copy. When a new version of snapgate ships:

```bash
cp -r snapgate/frontend/src/snapgate your-project/src/snapgate
```

Overwrite the entire directory. Do not patch individual files — the patterns
are interdependent.

## See Also

- `docs/postmortem-strictmode-auth-race.md` — detailed incident report on the StrictMode race bug
- `docs/testing-with-playwright.md` — how to verify the auth flow end-to-end
- `backend/snapgate/README.md` — backend integration docs

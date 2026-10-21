# Snapgate Frontend — Usage Examples

Practical code examples for common integration scenarios.

## Import the Stylesheet

The components ship their own CSS. Import it once, from wherever your app
collects its stylesheets:

```css
/* src/styles/main.css */
@import '../snapgate/snapgate.css';
```

Or, if your app imports CSS from TypeScript instead:

```ts
// main.tsx
import './snapgate/snapgate.css';
```

That is the only integration step beyond copying the directory. Every class in
the file is prefixed `sg-`, so it cannot collide with your own styles, and the
package borrows nothing from your utility or component CSS.

## Theming the Components

Every value in `snapgate.css` reads a `--sg-*` custom property with a built-in
fallback, so the components look right out of the box and adopt your palette as
soon as you define those properties. Define whichever ones you care about
anywhere that inherits down to the components — usually `:root` in your tokens
file — and leave the rest to their defaults.

### Colors

| Property | Default | Used for |
|---|---|---|
| `--sg-color-text` | `rgb(17 24 39)` | Primary text, menu items, names |
| `--sg-color-muted` | `rgb(107 114 128)` | Email addresses, section labels |
| `--sg-color-muted-strong` | `rgb(75 85 99)` | Body copy, secondary buttons, avatar initials |
| `--sg-color-faint` | `rgb(156 163 175)` | Dropdown caret, "forget account" control |
| `--sg-color-surface` | `rgb(255 255 255)` | Dropdown and card backgrounds |
| `--sg-color-surface-alt` | `rgb(249 250 251)` | Menu header, hovered account row, note block |
| `--sg-color-hover` | `rgb(243 244 246)` | Hover background on triggers and menu items |
| `--sg-color-border` | `rgb(229 231 235)` | All hairlines |
| `--sg-color-avatar-bg` | `rgb(209 213 219)` | Fallback avatar (no picture) background |
| `--sg-color-primary` | `rgb(37 99 235)` | Default button fill, focus ring |
| `--sg-color-on-primary` | `rgb(255 255 255)` | Text on the primary fill |
| `--sg-color-warning` | `rgb(202 138 4)` | "Pending approval" icon and status text, pending pills and chips |
| `--sg-color-success` | `rgb(22 163 74)` | Approved and allowed pills, held-role chips |
| `--sg-color-danger` | `rgb(220 38 38)` | Suspended, refused and rejected pills, admin error lines, field errors |
| `--sg-color-admin` | `rgb(124 58 237)` | Superadmin and Admin chips (tone `admin`) |

### Geometry

| Property | Default | Used for |
|---|---|---|
| `--sg-radius` | `0.5em` | Buttons, dropdowns, cards, account rows |
| `--sg-radius-sm` | `0.375em` | Menu items, note block |
| `--sg-border-width` | `thin` | All borders |
| `--sg-shadow` | two-layer soft drop shadow | Dropdown panel, pending card |
| `--sg-menu-width` | `18em` | Dropdown panel width |
| `--sg-card-width` | `28em` | Pending-approval card max width |
| `--sg-admin-width` | `64em` | Max width of the admin pages |
| `--sg-focus-width` | `0.125em` | Focus ring thickness |
| `--sg-focus-offset` | `0.125em` | Focus ring offset |

### Rhythm

| Property | Default |
|---|---|
| `--sg-space-1` | `0.25em` |
| `--sg-space-2` | `0.5em` |
| `--sg-space-3` | `0.75em` |
| `--sg-space-4` | `1em` |
| `--sg-space-6` | `1.5em` |
| `--sg-space-8` | `2em` |

### Type, layering, motion

| Property | Default | Used for |
|---|---|---|
| `--sg-font-family` | `inherit` | Font stack for all components |
| `--sg-font-size-xs` | `0.75em` | Uppercase labels |
| `--sg-font-size-sm` | `0.875em` | Email addresses, secondary text |
| `--sg-font-size-lg` | `1.125em` | Remembered-accounts heading |
| `--sg-font-size-2xl` | `1.5em` | Pending-approval heading |
| `--sg-font-weight-medium` | `500` | Names, buttons |
| `--sg-font-weight-bold` | `700` | Pending-approval heading |
| `--sg-z-overlay` | `1000` | Click-outside catcher |
| `--sg-z-menu` | `1001` | Dropdown panel |
| `--sg-transition` | `120ms ease` | Hover and reveal transitions |

Lengths default to `em` so the components scale with your base font size rather
than imposing one. Override them with any unit you like.

### Worked example

Mapping onto an app whose tokens are RGB triplets:

```css
/* src/styles/tokens.css */
:root {
  --sg-color-text: rgb(var(--color-text));
  --sg-color-muted: rgb(var(--color-text-light));
  --sg-color-muted-strong: rgb(var(--color-text-muted));
  --sg-color-faint: rgb(var(--color-gray-400));
  --sg-color-surface: rgb(var(--color-background));
  --sg-color-surface-alt: rgb(var(--color-background-alt));
  --sg-color-hover: rgb(var(--color-gray-100));
  --sg-color-border: rgb(var(--color-border));
  --sg-color-avatar-bg: rgb(var(--color-gray-300));
  --sg-color-primary: rgb(var(--color-primary));
  --sg-color-on-primary: rgb(var(--color-white));
  --sg-color-warning: rgb(var(--color-warning));
  --sg-radius: var(--radius-lg);
  --sg-radius-sm: var(--radius-md);
  --sg-font-family: var(--font-family-base);
}
```

### Bringing your own button

`LoginButton` still takes a `className`, and passing one replaces the package's
default button styling entirely:

```tsx
<LoginButton className="btn btn--primary">Sign in</LoginButton>
```

`AccountSwitcher`'s `className` is additive instead — it is appended to
`sg-account-switcher`, so use it for placement.

## Basic Setup

Wrap your app in `SnapgateProvider` and use `useSnapgate()` anywhere inside:

```tsx
// main.tsx
import { BrowserRouter } from 'react-router-dom';
import { SnapgateProvider } from './snapgate';
import App from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <SnapgateProvider apiUrl={import.meta.env.VITE_API_URL}>
        <App />
      </SnapgateProvider>
    </BrowserRouter>
  </React.StrictMode>
);
```

```tsx
// App.tsx
import { Routes, Route, Navigate } from 'react-router-dom';
import { ProtectedRoute } from './snapgate';
import LoginPage from './pages/LoginPage';
import Dashboard from './pages/Dashboard';

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={
        <ProtectedRoute requireApproval>
          <Dashboard />
        </ProtectedRoute>
      } />
    </Routes>
  );
}
```

## Login Page

```tsx
import { Navigate } from 'react-router-dom';
import { useSnapgate, LoginButton, AccountSwitcher } from './snapgate';

export default function LoginPage() {
  const { user, loading } = useSnapgate();

  if (loading) return <div>Loading…</div>;
  if (user) return <Navigate to="/" replace />;

  return (
    <div>
      <h1>Sign in</h1>
      {/* AccountSwitcher shows remembered accounts for quick re-login */}
      <AccountSwitcher />
      {/* LoginButton starts the sign-in (OpenID Connect) flow */}
      <LoginButton />
    </div>
  );
}
```

## Custom Error Handling

Pass an `onError` prop to `SnapgateProvider` to handle auth errors globally:

```tsx
import { SnapgateProvider } from './snapgate';
import type { AuthError } from './snapgate';

function handleAuthError(error: AuthError) {
  switch (error.code) {
    case 'token_rotation_failed':
      // Refresh token is expired or revoked — must re-authenticate
      console.warn('Session expired, redirecting to login');
      break;
    case 'rate_limited':
      // Too many refresh attempts — back off
      console.warn('Rate limit hit on token refresh');
      break;
    case 'network_error':
      // Server unreachable — show offline indicator, do not redirect
      showOfflineBanner();
      break;
    default:
      console.error('Auth error:', error.code, error.message);
  }
}

function Root() {
  return (
    <SnapgateProvider apiUrl={API_URL} onError={handleAuthError}>
      <App />
    </SnapgateProvider>
  );
}
```

## Reading Auth State

```tsx
import { useSnapgate } from './snapgate';

function ProfileBadge() {
  const { user, loading, error } = useSnapgate();

  if (loading) return <Spinner />;
  if (error) return <div>Auth error: {error.message}</div>;
  if (!user) return null;

  return (
    <div>
      <img src={user.picture} alt={user.name} />
      <span>{user.name}</span>
      <span>{user.status}</span>   {/* pending | approved | denied | suspended */}
    </div>
  );
}
```

## Pending Approval State

New users start `pending`. Use the `PendingApproval` component or check `user.status`:

```tsx
import { useSnapgate, PendingApproval } from './snapgate';

function Dashboard() {
  const { user } = useSnapgate();
  if (user?.status === 'pending') return <PendingApproval />;
  return <MainContent />;
}
```

Or use `ProtectedRoute` which handles it automatically:

```tsx
<ProtectedRoute requireApproval>
  {/* Only renders if user is approved */}
  <Dashboard />
</ProtectedRoute>
```

## Logout

```tsx
import { useSnapgate } from './snapgate';

function LogoutButton() {
  const { logout } = useSnapgate();
  return <button onClick={logout}>Sign out</button>;
}
```

## Demo Login (Development Only)

Enable the demo login feature in dev to bypass OAuth:

```tsx
<SnapgateProvider apiUrl={API_URL} enableDemoLogin={import.meta.env.DEV}>
  <App />
</SnapgateProvider>
```

```tsx
import { useSnapgate } from './snapgate';

function DevTools() {
  const { demoLogin } = useSnapgate();
  if (!demoLogin) return null;  // not enabled in production
  return (
    <div>
      <button onClick={() => demoLogin('admin')}>Login as Admin</button>
      <button onClick={() => demoLogin('user')}>Login as User</button>
    </div>
  );
}
```

The backend must have `DEMO_LOGIN=true` for demo login endpoints to be active.

## Connected Apps (OAuth connections)

When the backend mounts snapgate's OAuth server (`create_oauth_router`), chat
apps such as claude.ai can connect to the account. `ConnectionsPanel` is a
self-contained "Connected apps" section for a Settings page. It lists the live
connections, lets the person change the permissions each one holds (checkboxes, Save),
and revoke it (with a confirm step). A fixed connection (`fixed: true`, the
app's own command-line login, `OAuthServerConfig.native_client`) shows its
sign-in, last-used and lapse dates and only a Revoke button.

```tsx
import { ConnectionsPanel } from './snapgate';

export function SettingsPage() {
  return (
    <>
      {/* ...API keys, profile, etc. */}
      <ConnectionsPanel />
    </>
  );
}
```

Props (all optional):

| Prop | Default | Meaning |
|------|---------|---------|
| `title` | `"Connected apps"` | Heading text |
| `emptyText` | `"No AI apps are connected."` | Shown when nothing is connected |
| `className` | none | Extra class on the outer `<section>` |

Each row shows the app's name, "described by {host}" for an app identified by
a metadata document (else "registered app"), one checkbox per
permission group from `GET /auth/scopes` (ticked for the groups the connection
holds; Save is disabled when nothing changed or nothing is ticked), the scope descriptions, and "Connected {date} · Last active {date}".

Classes, all in `snapgate.css` and themed by the same `--sg-*` properties:
`sg-connections`, `sg-connections__title`, `__empty`, `__error`, `__list`,
`__item`, `__head`, `__name`, `__host`, `__groups`, `__legend`, `__group`,
`__save`, `__scopes`, `__dates`, `__actions`, `__confirm`,
`__revoke`. Buttons reuse `sg-button` / `sg-button--secondary`.

The same calls are on `AuthAPI` for custom UIs: `getScopes()`,
`listConnections()`, `updateConnection(id, groupIds)` and
`revokeConnection(id)`. The connection and API-key calls retry once after a
single-flight token refresh on a 401. `useSnapgate()` also exposes `apiUrl` now.

## User admin (optional module)

Site admins, account approve / suspend, a sign-in log, and applications for
app roles ("writer", "editor"). Nothing here runs unless you opt in on both
sides; an app that does not sees no extra requests and no new UI.

**The guard hides UI only.** The server checks every admin call: the caller
must be an approved, env-listed admin on a browser session. Admin endpoints
refuse API keys.

### 1. Backend

Mount `create_useradmin_router`, set `SNAPGATE_SUPERADMIN_EMAILS` (and
optionally `SNAPGATE_ADMIN_EMAILS`), and register your roles with `RoleSpec`.
See "User admin (optional)" in the backend README; it is not repeated here.

### 2. Turn it on

```tsx
<SnapgateProvider apiUrl={API_URL} userAdmin>
  <App />
</SnapgateProvider>
```

`user.site_role` (`'superadmin' | 'admin' | null`) is on `/auth/me` either
way. `userAdmin` adds one shared `GET /auth/roles` per page for app roles.

### 3. Role badge and `useSiteRole()`

`UserMenu` shows `<RoleBadge />` under the email, and a compact site-role chip
on the trigger for admins. Place it yourself anywhere:

```tsx
import { RoleBadge, useSiteRole } from './snapgate';

<RoleBadge />                        // "Admin", "Editor", "Pending: Editor"
<RoleBadge showSiteRole={false} />   // app roles only

const { siteRole, isAdmin, isSuperadmin, roles, held, pending,
        holds, pendingFor, loading, error, refresh } = useSiteRole();
```

| Field | Meaning |
|---|---|
| `siteRole`, `isAdmin`, `isSuperadmin` | From `/auth/roles` when loaded, else `user.site_role` |
| `roles` | App roles people may apply for (`[]` when the module is off) |
| `held` | App roles the user holds, with labels |
| `pending` | The user's pending applications |
| `holds(role)`, `pendingFor(role)` | Quick checks for one role |
| `loading`, `error`, `refresh()` | First load in flight; last error; drop the cache and reload |

A 404 from `/auth/roles` (frontend opted in, backend router missing) is
treated as "module off": no error, no retry loop.

### 4. Admin routes

```tsx
import { Navigate, Route } from 'react-router';
import { AdminOnlyRoute, AdminUsersPage, AdminRoleRequestsPage, AdminLoginsPage } from './snapgate';

const userHref = (id: number) => `/admin/users?user=${id}`;

<Route path="/admin/users" element={<AdminOnlyRoute><AdminUsersPage /></AdminOnlyRoute>} />
<Route path="/admin/requests" element={<AdminOnlyRoute><AdminRoleRequestsPage userHref={userHref} /></AdminOnlyRoute>} />
<Route path="/admin/logins" element={<AdminOnlyRoute><AdminLoginsPage userHref={userHref} /></AdminOnlyRoute>} />
<Route path="/admin" element={<Navigate to="/admin/users" replace />} />
```

`AdminOnlyRoute` props: `level` (`'admin'` default, or `'superadmin'`),
`redirectTo` (signed-in non-admins, default `/`), `loginPath` (default
`/login`, with `?returnTo=`). It reads `user.site_role`, so it needs no fetch.

Page state lives in the URL query, so views can be linked and Back works:

- `AdminUsersPage`: `?q=` (search, debounced), `?status=`, `?offset=`, and
  `?user=<id>` for the detail view (sign-ins, role applications, audit log).
  Suspend asks for a reason in an inline panel. Env-listed people show "Set by
  server config" instead of actions; your own row has no Suspend.
- `AdminRoleRequestsPage`: `?status=` (pending by default; approved, rejected,
  withdrawn) and `?role=`. Pending cards take an optional note and Approve /
  Reject. There is no Deny button for accounts.
- `AdminLoginsPage`: `?user_id=`; a client-side Allowed / Refused filter; "Show
  more" raises the limit (100, 250, 500).

All three take `title`, `className` and `onChanged()`. `onChanged` runs after
any successful action (the logins page never calls it), so an app can refetch
its own `/me` or nav counts:

```tsx
<AdminRoleRequestsPage onChanged={refetchPendingCount} />
```

### 5. An apply page with `RoleRequestForm`

The form collects and sends one application: your fields, a message box, and
the server's errors. Your page decides whether to show it.

```tsx
import { RoleRequestForm, useSiteRole, useUserAdminAPI } from './snapgate';

type Writer = { handle: string; display_name: string };

export function ApplyPage() {
  const { holds, pendingFor, refresh } = useSiteRole();
  const api = useUserAdminAPI();
  const pending = pendingFor('writer');

  if (holds('writer')) return <p>You are a writer.</p>;
  if (pending) {
    return (
      <p>
        Your application is waiting for review.{' '}
        <button onClick={() => api.withdrawRoleRequest(pending.id).then(refresh)}>Withdraw</button>
      </p>
    );
  }
  return (
    <RoleRequestForm<Writer>
      role="writer"
      initialDetails={{ handle: '', display_name: '' }}
      messageLabel="Why do you want a writer account?"
      renderDetails={({ details, setDetail, errors, disabled }) => (
        <>
          <label className="sg-field">
            <span className="sg-field__label">Handle</span>
            <input className="sg-field__input" value={details.handle} disabled={disabled}
                   onChange={(e) => setDetail('handle', e.target.value)} />
            {errors.handle && <span className="sg-field__error">{errors.handle}</span>}
          </label>
          <label className="sg-field">
            <span className="sg-field__label">Display name</span>
            <input className="sg-field__input" value={details.display_name} disabled={disabled}
                   onChange={(e) => setDetail('display_name', e.target.value)} />
          </label>
        </>
      )}
      onSubmitted={() => { /* show "sent" your way */ }}
    />
  );
}
```

Props: `role`, `initialDetails`, `renderDetails`, `messageLabel` (default
"Anything the reviewer should know?"), `submitLabel` (default "Send
application"), `onSubmitted(request)`. The message is sent as the top-level
`message`; `details` is your object. An `invalid_input` error whose `field` is
a key of `details` lands in `errors[field]` (editing the field clears it);
`field: 'message'` shows under the message box; anything else shows in the
form's error line. After a successful submit the form calls
`invalidateRoles()`, so badges show "Pending: ..." at once.

The `sg-field`, `sg-field__label`, `sg-field__input`, `sg-field__hint` and
`sg-field__error` classes are optional; your fields may use your own classes.

### 6. Error codes

Errors are `AuthError` (`{code, message, status, field?}`). Every component
shows `message` exactly as the server sent it; the server's messages explain
their own fix. Codes you may see:

`not_admin`, `session_required`, `account_not_eligible`, `env_admin`,
`self_action`, `already_suspended`, `not_suspended`, `unknown_role`,
`already_holds`, `request_pending`, `not_pending`, `not_your_request`,
`invalid_input` (422, may carry `field`), `not_found`.

App hooks may raise their own (Pigeon: `new_writers_closed`, `handle_taken`).
Unknown codes are shown by their `message`. `already_suspended` /
`not_suspended` and `not_pending` (another admin got there first) make the
page re-fetch instead of showing an error.

### 7. Custom detail rendering

The queue shows each application's `details` as a key/value list (keys
humanized, values as text, objects as JSON, never HTML). Override per role,
and link names to your user page:

```tsx
<AdminRoleRequestsPage
  renderDetails={{ writer: (r) => <p>@{String(r.details.handle)}</p> }}
  userHref={(id) => `/app/admin?user=${id}`}
/>
```

### 8. Apps that know roles from their own `/me`

Pass the chips and skip `/auth/roles` entirely:

```tsx
const chips = me.pending_role_requests.map((r) => ({
  label: `Pending: ${r.label}`, tone: 'pending' as const, to: '/app/apply',
}));
<UserMenu badge={<RoleBadge roles={me.writer ? [{ label: 'Letter writer', tone: 'success' }] : chips} />} />
```

`badge={null}` hides the badge. The site role chip still comes from
`user.site_role` unless `showSiteRole={false}`.

### 9. Calling the API directly

```tsx
import { useUserAdminAPI, snapgateFetch, readJson } from './snapgate';

const api = useUserAdminAPI();
await api.listUsers({ q: 'sam', status: 'pending' });   // {users, total}
await api.suspendUser(3, 'Spam');                       // AdminUser
await api.listRoleRequests({ status: 'all' });          // RoleRequest[]

// Your own API client, with snapgate's bearer header and refresh-on-401:
const res = await snapgateFetch(apiUrl, '/api/v1/me');
const me = await readJson<Me>(res);
```

Methods: `listUsers`, `getUser`, `approveUser`, `suspendUser`,
`unsuspendUser`, `listLogins`, `listRoleRequests`, `approveRoleRequest`,
`rejectRoleRequest`, `getRoles`, `myRoleRequests`, `submitRoleRequest`,
`withdrawRoleRequest`. They unwrap the server's envelopes.

### 10. Styling

New tokens: `--sg-color-success`, `--sg-color-danger`, `--sg-color-admin`,
`--sg-admin-width` (see the tables above). Classes: `sg-role-badge` (with
`__item--neutral|admin|pending|success|link`, `--compact`), `sg-admin` (and
`__header`, `__title`, `__toolbar`, `__search`, `__filter`, `__count`,
`__error`, `__empty`, `__pager`, `__back`, `__section`, `__section-title`),
`sg-admin-table` inside `sg-admin-table-wrap`, `sg-status--*`, `sg-admin-card`,
`sg-admin-action-panel`, `sg-tabs` / `sg-tabs__tab--active`, `sg-field*` and
`sg-role-request*`.

For tests, `SnapgateContext` is exported so a harness can supply a fixed
context value without the OAuth start-up; apps use `SnapgateProvider`.

## Standalone Utilities (No React)

Use `scheduleTokenRefresh`, `classifyAuthError`, and `isNetworkError` from
`utils.ts` without the React context — useful in vanilla TS services, workers,
or SSR code:

```ts
import { scheduleTokenRefresh, classifyAuthError, isNetworkError } from './snapgate/utils';

let cancelRefresh: () => void;

async function storeAndSchedule(tokens: { access_token: string; refresh_token: string; access_expires_at: string }) {
  localStorage.setItem('access_token', tokens.access_token);
  localStorage.setItem('refresh_token', tokens.refresh_token);

  if (cancelRefresh) cancelRefresh();  // cancel previous timer

  cancelRefresh = scheduleTokenRefresh(
    tokens.access_expires_at,
    async () => {
      const newTokens = await myRefreshFunction(tokens.refresh_token);
      storeAndSchedule(newTokens);
    },
    (err) => {
      const authErr = classifyAuthError(err);
      if (isNetworkError(authErr)) {
        console.warn('Offline — will retry refresh later');
      } else {
        clearTokens();
        redirectToLogin();
      }
    },
  );
}
```

## Testing Patterns

See `docs/testing-with-playwright.md` for the full workflow.

Quick smoke test for the OAuth callback:

```ts
// Verify tokens are stored after callback
await page.goto('/auth/login/callback?code=...');
const accessToken = await page.evaluate(() => localStorage.getItem('snapgate_access_token'));
expect(accessToken).toBeTruthy();

// Verify the app doesn't redirect to /login after callback
await expect(page).not.toHaveURL(/\/login/);
```

Key thing to test: **run in dev mode with React StrictMode enabled**. The
StrictMode double-invoke is the condition that triggers the OAuth race bug
described in `docs/postmortem-strictmode-auth-race.md`. If you only test in
production mode, that bug is invisible.

// Main exports for @snapgate/react package
export { SnapgateProvider, useSnapgate } from './context';
export { LoginButton } from './components/LoginButton';
export { UserMenu } from './components/UserMenu';
export { AccountSwitcher } from './components/AccountSwitcher';
export { ProtectedRoute } from './components/ProtectedRoute';
export { PendingApproval } from './components/PendingApproval';
export { ConnectionsPanel } from './components/ConnectionsPanel';
export type { ConnectionsPanelProps } from './components/ConnectionsPanel';
export { AuthAPI } from './api';
export type {
  User,
  AuthState,
  AuthError,
  AuthContextValue,
  RememberedAccount,
  ApiKeyInfo,
  ApiKeyListResponse,
  ApiKeyCreateRequest,
  ApiKeyCreateResponse,
  RefreshResponse,
  ScopeInfo,
  ScopeGroupInfo,
  ScopesResponse,
  ProviderInfo,
  ConnectionInfo,
  ConnectionListResponse,
  SiteRole,
} from './types';

// User admin (optional; needs the backend's create_useradmin_router and <SnapgateProvider userAdmin>)
export { useSiteRole } from './admin/useSiteRole';
export type { SiteRoleState } from './admin/useSiteRole';
export { invalidateRoles, roleBadgeItems } from './admin/roles';
export type { RoleBadgeItem } from './admin/roles';
export { RoleBadge } from './admin/RoleBadge';
export type { RoleBadgeProps, AppRoleBadge } from './admin/RoleBadge';
export { AdminOnlyRoute } from './admin/AdminOnlyRoute';
export { AdminUsersPage } from './admin/AdminUsersPage';
export type { AdminUsersPageProps } from './admin/AdminUsersPage';
export { AdminRoleRequestsPage } from './admin/AdminRoleRequestsPage';
export type { AdminRoleRequestsPageProps } from './admin/AdminRoleRequestsPage';
export { AdminLoginsPage } from './admin/AdminLoginsPage';
export type { AdminLoginsPageProps } from './admin/AdminLoginsPage';
export { RoleRequestForm } from './admin/RoleRequestForm';
export type { RoleRequestFormProps, RoleRequestDetailsProps } from './admin/RoleRequestForm';
export { UserAdminAPI, useUserAdminAPI } from './admin/api';
export type {
  AdminUser,
  AdminUserDetail,
  LoginEvent,
  AuditEntry,
  AuditAction,
  RoleRequest,
  RoleRequestStatus,
  AppRoleInfo,
  RolesInfo,
  UserListQuery,
} from './admin/types';

// For tests and harnesses that supply a fixed context value; apps use SnapgateProvider.
export { SnapgateContext } from './context';
// For an app's own API client that wants snapgate's refresh-on-401.
export { snapgateFetch, readJson, expectOk } from './api';

// Remembered accounts utilities (for advanced use cases)
export {
  getRememberedAccounts,
  addRememberedAccount,
  removeRememberedAccount,
  clearRememberedAccounts,
} from './rememberedAccounts';

// Standalone utilities (usable without React context)
export { scheduleTokenRefresh, classifyAuthError, isNetworkError, readAuthError } from './utils';

// Single-flight, cross-tab token refresh — API clients should use this on 401
// (and to proactively revive suspended-tab sessions) so concurrent refreshes,
// in this tab or another, never double-rotate the refresh token.
// sessionWasLost() tells a login page the session ended by itself (refused
// refresh token), so it can offer "sign in again" instead of redirecting.
export {
  refreshAccessToken,
  sessionWasLost,
  markSessionLost,
  clearSessionLost,
} from './refreshClient';

// Token storage keys (for consumers who need direct localStorage access)
export { ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, ACCESS_EXPIRES_AT_KEY } from './constants';

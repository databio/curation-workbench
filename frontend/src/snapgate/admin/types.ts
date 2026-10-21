/**
 * Shapes returned by snapgate's user-admin routes (backend
 * snapgate.useradmin / create_useradmin_router). Times are ISO 8601 strings.
 */
import type { SiteRole, UserStatus } from '../types';

/** One row of GET /auth/admin/users (and the `user` of approve / suspend / unsuspend). */
export interface AdminUser {
  id: number;
  email: string;
  name: string;
  picture: string | null;
  status: UserStatus;
  /** Non-null means env-listed: the server refuses to suspend or approve them. */
  site_role: SiteRole | null;
  created_at: string | null;
  last_login: string | null;
  approved_at: string | null;
  login_count: number;
  last_sign_in_at: string | null;
  /** Role names with a pending application. */
  pending_requests: string[];
  /** From the newest status audit row (e.g. the suspend reason). */
  status_reason: string | null;
  status_changed_by: number | null;
  status_changed_at: string | null;
}

/** GET /auth/admin/users/{id}. */
export interface AdminUserDetail {
  user: AdminUser;
  logins: LoginEvent[];
  audit: AuditEntry[];
  role_requests: RoleRequest[];
}

/** One sign-in attempt, allowed or refused. */
export interface LoginEvent {
  id: number;
  /** Null when the attempt never matched an account. */
  user_id: number | null;
  email: string;
  name: string | null;
  allowed: boolean;
  /** Why it was refused (or how it was allowed): "pending", "suspended", "denied", ... */
  reason: string | null;
  ip: string | null;
  user_agent: string | null;
  /** "oidc" or "demo". */
  method: string;
  at: string;
}

export type AuditAction =
  | 'approve'
  | 'suspend'
  | 'unsuspend'
  | 'deny'
  | 'role_request_approve'
  | 'role_request_reject'
  | 'role_request_withdraw'
  | 'role_revoke';

export interface AuditEntry {
  id: number;
  user_id: number;
  actor_user_id: number | null;
  actor_label: string;
  action: AuditAction;
  details: Record<string, unknown>;
  at: string;
}

export type RoleRequestStatus = 'pending' | 'approved' | 'rejected' | 'withdrawn';

export interface RoleRequest<D = Record<string, unknown>> {
  id: number;
  user_id: number;
  user_email: string;
  user_name: string;
  role: string;
  role_label: string;
  status: RoleRequestStatus;
  message: string;
  details: D;
  created_at: string;
  decided_at: string | null;
  decided_by: number | null;
  decision_note: string | null;
}

/** An app role people may apply for. */
export interface AppRoleInfo {
  name: string;
  label: string;
}

/** GET /auth/roles: the caller's site role, the app roles, which they hold, and their pending requests. */
export interface RolesInfo {
  site_role: SiteRole | null;
  roles: AppRoleInfo[];
  held: string[];
  pending: RoleRequest[];
}

export interface UserListQuery {
  q?: string;
  status?: UserStatus;
  limit?: number;
  offset?: number;
}

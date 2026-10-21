import React from 'react';
import { Navigate, useLocation } from 'react-router';
import { useSnapgate } from '../context';
import type { SiteRole } from '../types';

interface AdminOnlyRouteProps {
  children: React.ReactNode;
  /** Minimum site role. Default 'admin'. */
  level?: SiteRole;
  /** Where a signed-in non-admin goes. Default '/'. */
  redirectTo?: string;
  /** Login path for signed-out users. Default '/login' (matches ProtectedRoute). */
  loginPath?: string;
}

const RANK: Record<SiteRole, number> = { admin: 1, superadmin: 2 };

/**
 * Route guard for admin screens, from user.site_role (no extra fetch, works
 * without `userAdmin`). It only hides UI: the server enforces the real check
 * on every admin endpoint.
 */
export function AdminOnlyRoute({
  children,
  level = 'admin',
  redirectTo = '/',
  loginPath = '/login',
}: AdminOnlyRouteProps) {
  const { user, loading } = useSnapgate();
  const location = useLocation();

  if (loading) {
    return (
      <div className="sg-loading">
        <p>Loading...</p>
      </div>
    );
  }

  if (!user) {
    const returnTo = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`${loginPath}?returnTo=${returnTo}`} replace />;
  }

  const rank = user.site_role ? RANK[user.site_role] : 0;
  if (user.status !== 'approved' || rank < RANK[level]) {
    return <Navigate to={redirectTo} replace />;
  }

  return <>{children}</>;
}

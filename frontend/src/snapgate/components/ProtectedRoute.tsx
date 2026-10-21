import React from 'react';
import { Navigate, useLocation } from 'react-router';
import { useSnapgate } from '../context';

interface ProtectedRouteProps {
  children: React.ReactNode;
  requireApproval?: boolean;
}

/**
 * Route guard: renders `children` only for a signed-in (and, optionally,
 * approved) user, redirecting otherwise.
 *
 * Styling comes from snapgate.css, which the host app imports once.
 */
export function ProtectedRoute({ children, requireApproval = false }: ProtectedRouteProps) {
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
    return <Navigate to={`/login?returnTo=${returnTo}`} replace />;
  }

  if (requireApproval && user.status !== 'approved') {
    return <Navigate to="/pending-approval" replace />;
  }

  return <>{children}</>;
}

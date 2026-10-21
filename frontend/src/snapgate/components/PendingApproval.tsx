import { useSnapgate } from '../context';
import { Navigate } from 'react-router';

interface PendingApprovalProps {
  approvedRedirect?: string;
  loginRedirect?: string;
}

/**
 * Holding screen for users whose account is awaiting administrator approval.
 *
 * Styling comes from snapgate.css, which the host app imports once.
 */
export function PendingApproval({
  approvedRedirect = '/dashboard',
  loginRedirect = '/login',
}: PendingApprovalProps = {}) {
  const { user, logout, refreshUser } = useSnapgate();

  if (!user) {
    return <Navigate to={loginRedirect} replace />;
  }

  if (user.status === 'approved') {
    return <Navigate to={approvedRedirect} replace />;
  }

  return (
    <div className="sg-pending">
      <div className="sg-pending__card">
        <svg
          className="sg-pending__icon"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
          />
        </svg>

        <h1 className="sg-pending__title">Pending Approval</h1>

        <p className="sg-pending__text">
          Your account is waiting for administrator approval. You'll be able to
          access the application once an admin reviews your request.
        </p>

        <div className="sg-pending__actions">
          <button
            onClick={refreshUser}
            className="sg-button sg-button--block"
          >
            Check Approval Status
          </button>

          <button
            onClick={logout}
            className="sg-button sg-button--secondary sg-button--block"
          >
            Sign Out
          </button>
        </div>

        <div className="sg-pending__note">
          <p>
            Signed in as: <strong>{user.email}</strong>
          </p>
        </div>
      </div>
    </div>
  );
}

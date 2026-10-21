// Superadmin: approve sign-ups (snapgate's user admin page, as in Pigeon).
import { Navigate } from 'react-router';
import { AdminUsersPage } from '../../snapgate';
import { useHostedSession } from './session';

export default function Accounts() {
  const { me } = useHostedSession();
  if (me && me.site_role !== 'superadmin') return <Navigate to="/" replace />;
  return (
    <div className="page">
      <AdminUsersPage title="Accounts" />
    </div>
  );
}

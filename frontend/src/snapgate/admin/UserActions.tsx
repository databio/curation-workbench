import { useState } from 'react';
import { useSnapgate } from '../context';
import type { AuthError } from '../types';
import { useUserAdminAPI } from './api';
import { errorText } from './format';
import type { AdminUser } from './types';

interface UserActionsProps {
  user: AdminUser;
  /** A successful action: the row as the server now has it. */
  onChange: (updated: AdminUser) => void;
  /** Another admin got there first (already_suspended / not_suspended): re-fetch. */
  onStale: () => void;
}

/** Approve / Suspend (with a reason, inline) / Unsuspend for one account. */
export function UserActions({ user, onChange, onStale }: UserActionsProps) {
  const api = useUserAdminAPI();
  const { user: me } = useSnapgate();
  const [busy, setBusy] = useState(false);
  const [suspending, setSuspending] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  if (user.site_role) {
    return (
      <div className="sg-admin-actions">
        <span className="sg-admin-table__cell--muted">Set by server config</span>
      </div>
    );
  }

  const run = async (action: () => Promise<AdminUser>) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await action();
      setSuspending(false);
      setReason('');
      onChange(updated);
    } catch (e) {
      const code = (e as AuthError)?.code;
      if (code === 'already_suspended' || code === 'not_suspended') {
        setSuspending(false);
        onStale();
      } else {
        setError(errorText(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const isSelf = me?.id === user.id;
  const canApprove = user.status === 'pending' || user.status === 'denied';
  const canSuspend = !isSelf && (user.status === 'approved' || user.status === 'pending');
  const canUnsuspend = user.status === 'suspended';

  return (
    <div className="sg-admin-actions">
      {canApprove && (
        <button
          type="button"
          className="sg-button"
          disabled={busy}
          onClick={() => run(() => api.approveUser(user.id))}
        >
          Approve
        </button>
      )}
      {canSuspend && !suspending && (
        <button
          type="button"
          className="sg-button sg-button--secondary"
          disabled={busy}
          onClick={() => setSuspending(true)}
        >
          Suspend…
        </button>
      )}
      {canUnsuspend && (
        <button
          type="button"
          className="sg-button sg-button--secondary"
          disabled={busy}
          onClick={() => run(() => api.unsuspendUser(user.id))}
        >
          Unsuspend
        </button>
      )}
      {canSuspend && suspending && (
        <div className="sg-admin-action-panel">
          <label className="sg-field">
            <span className="sg-field__label">Reason for suspending {user.name || user.email}</span>
            <textarea
              className="sg-field__input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={busy}
              rows={2}
              maxLength={500}
            />
          </label>
          <div className="sg-admin-action-panel__buttons">
            <button
              type="button"
              className="sg-button"
              disabled={busy || !reason.trim()}
              onClick={() => run(() => api.suspendUser(user.id, reason.trim()))}
            >
              Suspend
            </button>
            <button
              type="button"
              className="sg-button sg-button--secondary"
              disabled={busy}
              onClick={() => {
                setSuspending(false);
                setReason('');
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && (
        <p className="sg-admin__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

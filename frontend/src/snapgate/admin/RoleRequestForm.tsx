import React, { useState } from 'react';
import type { AuthError } from '../types';
import { useUserAdminAPI } from './api';
import { errorText } from './format';
import { invalidateRoles } from './roles';
import type { RoleRequest } from './types';

export interface RoleRequestDetailsProps<D> {
  details: D;
  setDetail: <K extends keyof D>(key: K, value: D[K]) => void;
  /** Field-keyed errors: from invalid_input's `field`. */
  errors: Partial<Record<keyof D, string>>;
  disabled: boolean;
}

export interface RoleRequestFormProps<D extends Record<string, unknown>> {
  /** The role to apply for, e.g. "writer". */
  role: string;
  initialDetails: D;
  /** The app's own fields for `details`. */
  renderDetails: (p: RoleRequestDetailsProps<D>) => React.ReactNode;
  /** Default "Anything the reviewer should know?". */
  messageLabel?: string;
  /** Default "Send application". */
  submitLabel?: string;
  onSubmitted?: (request: RoleRequest<D>) => void;
}

/**
 * Collects and sends one role application: the app's fields (renderDetails),
 * a message box, and the server's errors (invalid_input with a `field` goes
 * to that field). It does not decide whether to show itself: the app checks
 * useSiteRole().holds(role) / pendingFor(role) and shows those states.
 */
export function RoleRequestForm<D extends Record<string, unknown>>({
  role,
  initialDetails,
  renderDetails,
  messageLabel = 'Anything the reviewer should know?',
  submitLabel = 'Send application',
  onSubmitted,
}: RoleRequestFormProps<D>) {
  const api = useUserAdminAPI();
  const [details, setDetails] = useState<D>(initialDetails);
  const [message, setMessage] = useState('');
  const [errors, setErrors] = useState<Partial<Record<keyof D, string>>>({});
  const [messageError, setMessageError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const setDetail = <K extends keyof D>(key: K, value: D[K]) => {
    setDetails((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErrors({});
    setMessageError(null);
    setFormError(null);
    try {
      const request = await api.submitRoleRequest<D>(role, message, details);
      invalidateRoles();
      onSubmitted?.(request);
    } catch (err) {
      const { code, field } = (err as AuthError) ?? {};
      const text = errorText(err);
      if (code === 'invalid_input' && field && field in details) {
        setErrors({ [field]: text } as Partial<Record<keyof D, string>>);
      } else if (code === 'invalid_input' && field === 'message') {
        setMessageError(text);
      } else {
        setFormError(text);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="sg-role-request" onSubmit={submit} noValidate>
      {renderDetails({ details, setDetail, errors, disabled: busy })}
      <label className="sg-field sg-role-request__message">
        <span className="sg-field__label">{messageLabel}</span>
        <textarea
          className="sg-field__input"
          value={message}
          onChange={(e) => {
            setMessage(e.target.value);
            setMessageError(null);
          }}
          disabled={busy}
          rows={4}
          maxLength={2000}
        />
        {messageError && <span className="sg-field__error">{messageError}</span>}
      </label>
      {formError && (
        <p className="sg-role-request__error" role="alert">
          {formError}
        </p>
      )}
      <div className="sg-role-request__actions">
        <button type="submit" className="sg-button" disabled={busy}>
          {submitLabel}
        </button>
      </div>
    </form>
  );
}

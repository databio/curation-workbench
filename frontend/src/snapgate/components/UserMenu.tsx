import React, { useState } from 'react';
import { useSnapgate } from '../context';
import { RoleBadge } from '../admin/RoleBadge';

interface UserMenuProps {
  /** What to show under the email in the menu header. Default <RoleBadge />. Pass null to hide. */
  badge?: React.ReactNode;
}

/**
 * Signed-in user menu: avatar plus name, with a dropdown carrying the
 * account details, the role badge and a sign-out action. A site admin also
 * gets a compact role chip on the trigger.
 *
 * Styling comes from snapgate.css, which the host app imports once.
 */
export function UserMenu({ badge = <RoleBadge /> }: UserMenuProps = {}) {
  const { user, logout } = useSnapgate();
  const [isOpen, setIsOpen] = useState(false);

  if (!user) return null;

  return (
    <div className="sg-user-menu">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="sg-trigger"
        aria-expanded={isOpen}
        aria-haspopup="true"
      >
        {user.picture && (
          <img
            src={user.picture}
            alt={user.name}
            className="sg-avatar"
          />
        )}
        <span className="sg-trigger__name">{user.name}</span>
        {user.site_role && <RoleBadge roles={[]} className="sg-role-badge--compact" />}
      </button>

      {isOpen && (
        <>
          <div
            className="sg-overlay"
            onClick={() => setIsOpen(false)}
            aria-hidden="true"
          />
          <div className="sg-menu">
            <div className="sg-menu__header">
              <p className="sg-identity__name">{user.name}</p>
              <p className="sg-identity__email">{user.email}</p>
              {badge}
              {user.status !== 'approved' && (
                <p className="sg-menu__status">Pending Approval</p>
              )}
            </div>
            <div className="sg-menu__section">
              <button
                onClick={() => {
                  setIsOpen(false);
                  logout();
                }}
                className="sg-menu__item"
              >
                Sign Out
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

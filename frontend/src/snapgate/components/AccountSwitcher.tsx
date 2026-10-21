import { useState, useMemo } from 'react';
import { useSnapgate } from '../context';

interface AccountSwitcherProps {
  className?: string;
}

/**
 * In-app account switcher component.
 *
 * Shows the current user with a dropdown to switch to other remembered accounts.
 * Appears in the navbar/header when the user is logged in.
 *
 * Styling comes from snapgate.css, which the host app imports once.
 */
export function AccountSwitcher({ className }: AccountSwitcherProps) {
  const { user, login, rememberedAccounts } = useSnapgate();
  const [isOpen, setIsOpen] = useState(false);

  // Filter out the current user from remembered accounts
  const otherAccounts = useMemo(() =>
    rememberedAccounts.filter(a => a.google_id !== user?.google_id),
    [rememberedAccounts, user?.google_id]
  );

  if (!user || otherAccounts.length === 0) {
    return null; // No other accounts to switch to
  }

  const handleSwitch = (email: string) => {
    setIsOpen(false);
    // Pass login_hint so the issuer can pre-select the account
    login(email);
  };

  return (
    <div className={className ? `sg-account-switcher ${className}` : 'sg-account-switcher'}>
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
        <span className="sg-trigger__caret">▼</span>
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
              <p className="sg-menu__label">Signed in as</p>
              <p className="sg-identity__name">{user.email}</p>
            </div>

            <div className="sg-menu__section">
              <p className="sg-menu__section-label">
                Switch account
              </p>
              {otherAccounts.map(account => (
                <button
                  key={account.google_id}
                  onClick={() => handleSwitch(account.email)}
                  className="sg-menu__item"
                >
                  {account.picture ? (
                    <img
                      src={account.picture}
                      alt={account.name}
                      className="sg-avatar"
                    />
                  ) : (
                    <div className="sg-avatar">
                      {account.name.charAt(0).toUpperCase()}
                    </div>
                  )}
                  <div className="sg-identity">
                    <p className="sg-identity__name">{account.name}</p>
                    <p className="sg-identity__email">{account.email}</p>
                  </div>
                </button>
              ))}
            </div>

            <div className="sg-menu__section">
              <button
                onClick={() => {
                  setIsOpen(false);
                  login(); // No login_hint - the issuer asks which account
                }}
                className="sg-menu__item sg-menu__item--muted"
              >
                Use a different account
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

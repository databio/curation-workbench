import React from 'react';
import { useSnapgate } from '../context';

interface LoginButtonProps {
  className?: string;
  children?: React.ReactNode;
  /** If true, show remembered accounts list instead of just a button */
  showRememberedAccounts?: boolean;
  /** Custom title for remembered accounts section */
  rememberedAccountsTitle?: string;
}

/**
 * Login button with optional remembered accounts list.
 *
 * When `showRememberedAccounts` is true and there are remembered accounts,
 * displays a list of accounts for one-click login, plus an option to use
 * a different account.
 *
 * Styling comes from snapgate.css, which the host app imports once. Passing
 * `className` replaces the package's own button styling with the host's.
 */
export function LoginButton({
  className,
  children,
  showRememberedAccounts = false,
  rememberedAccountsTitle = 'Sign in as:',
}: LoginButtonProps) {
  const { login, loading, rememberedAccounts, removeRememberedAccount, providerLabel } =
    useSnapgate();

  // Show remembered accounts list if enabled and accounts exist
  if (showRememberedAccounts && rememberedAccounts.length > 0) {
    return (
      <div className="sg-remembered">
        <h3 className="sg-remembered__title">
          {rememberedAccountsTitle}
        </h3>

        <div className="sg-remembered__list">
          {rememberedAccounts.map(account => (
            <div
              key={account.google_id}
              className="sg-remembered__item"
            >
              <button
                onClick={() => login(account.email)}
                disabled={loading}
                className="sg-remembered__pick"
              >
                {account.picture ? (
                  <img
                    src={account.picture}
                    alt={account.name}
                    className="sg-avatar sg-avatar--lg"
                  />
                ) : (
                  <div className="sg-avatar sg-avatar--lg">
                    {account.name.charAt(0).toUpperCase()}
                  </div>
                )}
                <div className="sg-identity">
                  <p className="sg-identity__name">{account.name}</p>
                  <p className="sg-identity__email">{account.email}</p>
                </div>
              </button>

              <button
                onClick={() => removeRememberedAccount(account.google_id)}
                className="sg-remembered__forget"
                title="Forget this account"
                aria-label="Forget this account"
              >
                ×
              </button>
            </div>
          ))}
        </div>

        <button
          onClick={() => login()}
          disabled={loading}
          className="sg-link"
        >
          Use a different account
        </button>
      </div>
    );
  }

  // Default: simple login button
  return (
    <button
      onClick={() => login()}
      disabled={loading}
      className={className || 'sg-button'}
    >
      {children || (providerLabel ? `Sign in with ${providerLabel}` : 'Sign in')}
    </button>
  );
}

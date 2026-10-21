/**
 * Utilities for managing remembered accounts in localStorage.
 *
 * Remembered accounts allow one-click login for users who have previously
 * authenticated on this device.
 */

import { RememberedAccount, User } from './types';

const REMEMBERED_ACCOUNTS_KEY = 'snapgate_remembered_accounts';
const MAX_REMEMBERED_ACCOUNTS = 5;

/**
 * Get all remembered accounts from localStorage.
 */
export function getRememberedAccounts(): RememberedAccount[] {
  try {
    const stored = localStorage.getItem(REMEMBERED_ACCOUNTS_KEY);
    return stored ? JSON.parse(stored) : [];
  } catch (e) {
    console.warn('Failed to read remembered accounts:', e);
    return [];
  }
}

/**
 * Add or update a remembered account.
 * Most recently used account is moved to the front of the list.
 */
export function addRememberedAccount(user: User): void {
  try {
    const accounts = getRememberedAccounts()
      .filter(a => a.google_id !== user.google_id);

    const newAccount: RememberedAccount = {
      google_id: user.google_id,
      email: user.email,
      name: user.name,
      picture: user.picture,
      last_login: new Date().toISOString(),
    };

    accounts.unshift(newAccount); // Most recent first

    localStorage.setItem(
      REMEMBERED_ACCOUNTS_KEY,
      JSON.stringify(accounts.slice(0, MAX_REMEMBERED_ACCOUNTS))
    );
  } catch (e) {
    console.warn('Failed to save remembered account:', e);
  }
}

/**
 * Remove a remembered account by google_id.
 */
export function removeRememberedAccount(googleId: string): void {
  try {
    const accounts = getRememberedAccounts()
      .filter(a => a.google_id !== googleId);
    localStorage.setItem(REMEMBERED_ACCOUNTS_KEY, JSON.stringify(accounts));
  } catch (e) {
    console.warn('Failed to remove remembered account:', e);
  }
}

/**
 * Clear all remembered accounts.
 */
export function clearRememberedAccounts(): void {
  try {
    localStorage.removeItem(REMEMBERED_ACCOUNTS_KEY);
  } catch (e) {
    console.warn('Failed to clear remembered accounts:', e);
  }
}

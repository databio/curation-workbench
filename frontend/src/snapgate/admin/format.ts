/** Small pure helpers shared by snapgate's components. */
import type { AuthError, UserStatus } from '../types';
import type { AuditAction } from './types';

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** A local date ("3/14/2026"); the input itself if it does not parse; '' for null. */
export function formatDate(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? d.toLocaleDateString() : iso || '';
}

/** A local date and time; the input itself if it does not parse; '' for null. */
export function formatDateTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d
    ? d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    : iso || '';
}

/** The server's message for a thrown AuthError, or a generic fallback. */
export function errorText(e: unknown): string {
  const err = e as AuthError | undefined;
  return err?.message || 'Something went wrong. Please try again.';
}

const STATUS_LABELS: Record<UserStatus, string> = {
  pending: 'Pending',
  approved: 'Approved',
  suspended: 'Suspended',
  denied: 'Denied',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status as UserStatus] ?? humanizeKey(status);
}

const AUDIT_LABELS: Record<AuditAction, string> = {
  approve: 'Approved account',
  suspend: 'Suspended account',
  unsuspend: 'Unsuspended account',
  deny: 'Denied account',
  role_request_approve: 'Approved role application',
  role_request_reject: 'Rejected role application',
  role_request_withdraw: 'Withdrew role application',
  role_revoke: 'Revoked role',
};

export function auditActionLabel(action: string): string {
  return AUDIT_LABELS[action as AuditAction] ?? humanizeKey(action);
}

/** "display_name" -> "Display name"; "userAgent" -> "User agent". */
export function humanizeKey(key: string): string {
  const words = key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim()
    .toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : key;
}

/** A short "Browser on OS" for a user-agent string; '' for null. */
export function shortUserAgent(ua: string | null | undefined): string {
  if (!ua) return '';
  const browser =
    /Edg\//.test(ua) ? 'Edge'
    : /OPR\/|Opera/.test(ua) ? 'Opera'
    : /Firefox\//.test(ua) ? 'Firefox'
    : /Chrome\//.test(ua) ? 'Chrome'
    : /Safari\//.test(ua) ? 'Safari'
    : /curl\//i.test(ua) ? 'curl'
    : null;
  const os =
    /iPhone|iPad|iPod/.test(ua) ? 'iOS'
    : /Android/.test(ua) ? 'Android'
    : /Windows/.test(ua) ? 'Windows'
    : /Mac OS X|Macintosh/.test(ua) ? 'macOS'
    : /Linux/.test(ua) ? 'Linux'
    : null;
  if (browser && os) return `${browser} on ${os}`;
  if (browser || os) return (browser || os) as string;
  return ua.length > 40 ? `${ua.slice(0, 40)}…` : ua;
}

/** A detail value as plain text: strings as-is, everything else as JSON. */
export function valueText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

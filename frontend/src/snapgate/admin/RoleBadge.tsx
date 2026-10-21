import { Link } from 'react-router';
import { useSnapgate } from '../context';
import { roleBadgeItems } from './roles';
import { useRolesState } from './useSiteRole';

export interface AppRoleBadge {
  /** "Letter writer", "Pending: Letter writer". */
  label: string;
  /** Default 'neutral'. */
  tone?: 'neutral' | 'admin' | 'pending' | 'success';
  /** Optional link (react-router <Link>). */
  to?: string;
  /** Tooltip; defaults to the label. */
  title?: string;
}

export interface RoleBadgeProps {
  /** App-supplied badges. When given, they are shown after the site role chip (and /auth/roles is not used). */
  roles?: AppRoleBadge[];
  /** Show the site role chip ("Superadmin" / "Admin"). Default true. */
  showSiteRole?: boolean;
  className?: string;
}

const SITE_TITLES = {
  superadmin: 'Site superadmin (set by server config)',
  admin: 'Site admin (set by server config)',
} as const;

/**
 * Chips for the signed-in user's roles: the site role first (from
 * user.site_role, no fetch), then the app's `roles`, or else, with
 * <SnapgateProvider userAdmin>, the held and pending app roles from /auth/roles.
 * Renders nothing when there is nothing to show.
 */
export function RoleBadge({ roles, showSiteRole = true, className }: RoleBadgeProps) {
  const { user, userAdmin } = useSnapgate();
  const state = useRolesState(roles === undefined);
  if (!user) return null;

  const items: AppRoleBadge[] = [];
  if (showSiteRole && state.siteRole) {
    items.push({
      label: state.siteRole === 'superadmin' ? 'Superadmin' : 'Admin',
      tone: 'admin',
      title: SITE_TITLES[state.siteRole],
    });
  }
  if (roles) {
    items.push(...roles);
  } else if (userAdmin) {
    for (const item of roleBadgeItems({ siteRole: null, held: state.held, pending: state.pending })) {
      items.push(
        item.kind === 'pending'
          ? { label: item.label, tone: 'pending', title: 'Application waiting for review' }
          : { label: item.label, tone: 'success', title: `You hold the ${item.label} role` },
      );
    }
  }
  if (items.length === 0) return null;

  return (
    <span className={['sg-role-badge', className].filter(Boolean).join(' ')}>
      {items.map((item, i) => {
        const cls = `sg-role-badge__item sg-role-badge__item--${item.tone ?? 'neutral'}`;
        const title = item.title ?? item.label;
        return item.to ? (
          <Link key={i} to={item.to} className={`${cls} sg-role-badge__item--link`} title={title}>
            {item.label}
          </Link>
        ) : (
          <span key={i} className={cls} title={title}>
            {item.label}
          </span>
        );
      })}
    </span>
  );
}

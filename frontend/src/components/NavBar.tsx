// Project title, then Review / Apply / More; on the right, the mode chip
// (local: branch and who decides; hosted: the account menu and role).
import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router';
import { useBackend } from '../api/backend';
import { UserMenu } from '../snapgate';
import { useDecisions, useReview } from '../hooks/useData';
import { useResource } from '../hooks/useResource';
import { allPath, applyPath, findingsPath, historyPath, morePath, projectPath, teamPath } from '../lib/paths';

const navClass = ({ isActive }: { isActive: boolean }) => `nav__link${isActive ? ' nav__link--active' : ''}`;

function MoreMenu() {
  const { project, can } = useBackend();
  const review = useReview();
  const ref = useRef<HTMLDetailsElement>(null);
  const location = useLocation();
  useEffect(() => {
    if (ref.current) ref.current.open = false;
  }, [location.pathname]);
  return (
    <details ref={ref} className="menu">
      <summary className="nav__link">More</summary>
      <ul className="menu__list">
        <li><Link className="menu__item" to={historyPath(project.slug)}>History</Link></li>
        <li><Link className="menu__item" to={allPath(project.slug)}>All items</Link></li>
        <li><Link className="menu__item" to={findingsPath(project.slug)}>Findings</Link></li>
        {(review.data?.more_links ?? []).map((l) => (
          <li key={l.url}><a className="menu__item" href={l.url} target="_blank" rel="noreferrer">{l.label} ↗</a></li>
        ))}
        {can.adminTeam ? <li><Link className="menu__item" to={teamPath(project.slug)}>Team</Link></li> : null}
        {can.adminAccounts ? <li><Link className="menu__item" to="/admin/accounts">Accounts</Link></li> : null}
        <li><Link className="menu__item" to={morePath(project.slug)}>All of these…</Link></li>
      </ul>
    </details>
  );
}

function LocalChip() {
  const backend = useBackend();
  const health = useResource([backend.project.slug, 'health'], () => backend.health!(), { enabled: !!backend.health });
  const h = health.data;
  return (
    <span className="mode-chip" title="Decisions are written to this checkout's decisions.yaml">
      Local{h ? ` · branch ${h.git_branch}` : ''} · {backend.me.name}
      {h?.working_tree_dirty ? <span className="mode-chip__dirty"> · uncommitted changes</span> : null}
    </span>
  );
}

export function StaleBar() {
  const backend = useBackend();
  const review = useReview();
  const health = useResource([backend.project.slug, 'health'], () => backend.health!(), { enabled: !!backend.health });
  if (!review.data || !health.data || review.data.schema_fingerprint === health.data.schema_fingerprint) return null;
  return <div className="stale-bar" role="alert">These lists are older than the schema. Run cwb regen.</div>;
}

export function NavBar() {
  const { project, mode, me } = useBackend();
  const { pathname } = useLocation();
  const home = projectPath(project.slug);
  const onReview = pathname === home || pathname.startsWith(`${home}/review/`);
  const decisions = useDecisions();
  const pending = (decisions.data ?? []).filter((d) => d.status === 'pending').length;
  const [open, setOpen] = useState(false);
  const links = (
    <>
      <NavLink to={home} end className={() => navClass({ isActive: onReview })}>Review</NavLink>
      <NavLink to={applyPath(project.slug)} className={navClass}>
        Apply{pending ? <span className="nav__count" data-testid="pending-count">{pending}</span> : null}
      </NavLink>
      <MoreMenu />
    </>
  );
  return (
    <>
      <nav className="nav">
        <Link to={projectPath(project.slug)} className="nav__brand">{project.title}</Link>
        <div className="nav__links">{links}</div>
        <button type="button" className="btn btn--sm nav__toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          Menu
        </button>
        <span className="nav__spacer" />
        {mode === 'local' ? <LocalChip /> : (
          <span className="toolbar">
            {me.role ? <span className="badge badge--neutral">{me.role}</span> : null}
            <UserMenu badge={null} />
          </span>
        )}
      </nav>
      {open ? <div className="nav__mobile-links">{links}</div> : null}
      {mode === 'local' ? <StaleBar /> : null}
    </>
  );
}

/** Hosted pages outside a project: brand and account only. */
export function PlainNav() {
  return (
    <nav className="nav">
      <Link to="/" className="nav__brand">Curation review</Link>
      <span className="nav__spacer" />
      <UserMenu badge={null} />
    </nav>
  );
}

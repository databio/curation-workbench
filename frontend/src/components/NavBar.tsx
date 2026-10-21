// Project title, then Review / Approved / More; on the right, the person's
// role and account menu.
import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router';
import { useBackend } from '../api/backend';
import { UserMenu } from '../snapgate';
import { useDecisions, useReview } from '../hooks/useData';
import { allPath, approvedPath, findingsPath, historyPath, morePath, projectPath, teamPath } from '../lib/paths';

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
        <li><Link className="menu__item" to={morePath(project.slug)}>Everything under More…</Link></li>
      </ul>
    </details>
  );
}

export function NavBar() {
  const { project, me } = useBackend();
  const { pathname } = useLocation();
  const home = projectPath(project.slug);
  const onReview = pathname === home || pathname.startsWith(`${home}/review/`);
  const decisions = useDecisions();
  const pending = (decisions.data ?? []).filter((d) => d.status === 'pending').length;
  const [open, setOpen] = useState(false);
  const links = (
    <>
      <NavLink to={home} end className={() => navClass({ isActive: onReview })}>Review</NavLink>
      <NavLink to={approvedPath(project.slug)} className={navClass}>
        Approved{pending ? <span className="nav__count" data-testid="pending-count">{pending}</span> : null}
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
        <span className="toolbar">
          {me.role ? <span className="nav__role">{me.role}</span> : null}
          <UserMenu badge={null} />
        </span>
      </nav>
      {open ? <div className="nav__mobile-links">{links}</div> : null}
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

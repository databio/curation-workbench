import { Link } from 'react-router';
import { useBackend } from '../api/backend';
import type { SubjectIndex } from '../api/types';
import { subjectPath } from '../lib/paths';

/** The display name of a SID (a term's label, else its name). */
export function nameOf(sid: string, index: SubjectIndex | undefined): string {
  const r = index?.[sid];
  if (!r) return sid;
  return r.kind === 'term' ? r.label : r.name;
}

/** A link to a subject's page, by SID; plain text when the SID is unknown. */
export function SubjectLink({ sid, index, children, className }: {
  sid: string;
  index: SubjectIndex | undefined;
  children?: React.ReactNode;
  className?: string;
}) {
  const { project } = useBackend();
  const slug = index?.[sid]?.slug;
  const label = children ?? nameOf(sid, index);
  if (!slug) return <span className={className}>{label}</span>;
  return (
    <Link to={subjectPath(project.slug, slug)} className={className}>
      {label}
    </Link>
  );
}

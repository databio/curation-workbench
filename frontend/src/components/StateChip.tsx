import { Link } from 'react-router';
import { useBackend } from '../api/backend';
import type { SubjectIndex } from '../api/types';
import { subjectPath } from '../lib/paths';

/** A subject's review state as a colored chip; with `link`, a link to its page. */
export function StateChip({ sid, index, link = false }: { sid: string; index: SubjectIndex | undefined; link?: boolean }) {
  const { project } = useBackend();
  const row = index?.[sid];
  const state = row ? row.state : 'unknown';
  const cls = `state-chip state-chip--${state}`;
  const title = row ? `${sid}: ${state}` : `${sid}: not in the subject registry`;
  if (link && row?.slug) {
    return (
      <Link to={subjectPath(project.slug, row.slug)} className={cls} title={title}>
        {state}
      </Link>
    );
  }
  return (
    <span className={cls} title={title}>
      {state}
    </span>
  );
}

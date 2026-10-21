// Review home: one short sentence, one progress line, then one section per
// track and one card per review type (title, count, question).
import { Link } from 'react-router';
import { useBackend } from '../api/backend';
import { QueryState } from '../components/QueryState';
import { useReview } from '../hooks/useData';
import { allPath, typePath } from '../lib/paths';

export default function ReviewHome() {
  const { project } = useBackend();
  const review = useReview();
  const rv = review.data;

  return (
    <QueryState isPending={review.isPending} error={review.error} onRetry={review.refetch}>
      {rv ? (
        <div className="page">
          <div>
            <h1 className="m-0">Review</h1>
            <p className="page__lede mt-2 mb-0">
              Pick a kind of work. Each item asks one question; your answer is a vote (curators turn votes into
              decisions).
            </p>
            <p className="muted text-sm mt-1 mb-0" data-testid="progress">
              {rv.progress.waiting} items waiting · {rv.progress.percent_settled}% settled ·{' '}
              <Link to={allPath(project.slug)}>all items</Link>
            </p>
          </div>

          {rv.tracks.map((t) => (
            <section key={t.id} className="stack">
              <div>
                <h2 className="m-0">
                  {t.title} <span className="muted text-base font-normal">{t.total}</span>
                </h2>
                {t.blurb ? <p className="muted text-sm m-0">{t.blurb}</p> : null}
              </div>
              <div className="type-grid">
                {t.types.map((ty) => (
                  <Link key={ty.id} to={typePath(project.slug, t.id, ty.id)}
                    className={`type-card${ty.count === 0 ? ' type-card--empty' : ''}`}>
                    <div className="type-card__head">
                      <h3 className="m-0">{ty.title}</h3>
                      <span className="type-card__count" title={`${ty.count} items waiting`}>{ty.count}</span>
                    </div>
                    {ty.question ? <p className="type-card__question">{ty.question}</p> : null}
                  </Link>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : null}
    </QueryState>
  );
}

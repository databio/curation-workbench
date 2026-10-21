// Review home: one section per track, one card per review type with its
// question, usual answers and item count.
import { Link } from 'react-router';
import { useBackend } from '../api/backend';
import type { Question } from '../api/types';
import { QueryState } from '../components/QueryState';
import { useQuestions, useReview } from '../hooks/useData';
import { allPath, applyPath, typePath } from '../lib/paths';

export default function ReviewHome() {
  const { project, mode, can } = useBackend();
  const review = useReview();
  const questions = useQuestions();
  const rv = review.data;

  const hostedCounts = (reviewType: string) => {
    const qs = (questions.data ?? []).filter((q: Question) => q.review_type === reviewType);
    return {
      mine: qs.filter((q) => q.mine).length,
      waiting: qs.filter((q) => q.state === 'open' && q.total > 0).length,
    };
  };

  return (
    <QueryState isPending={review.isPending} error={review.error} onRetry={review.refetch}>
      {rv ? (
        <div className="page">
          <div>
            <h1 className="m-0">Review</h1>
            <p className="page__lede mt-2">
              Curating means checking each piece of {rv.title} and recording a decision about it. Much of a schema is
              drafted by scripts or copied from elsewhere, so a person needs to confirm it. Pick one kind of work; every
              item on its page asks the same question.
            </p>
            <p className="muted text-sm mt-2 mb-0" data-testid="progress">
              {rv.progress.waiting} items waiting · {rv.progress.percent_settled}% settled.{' '}
              {mode === 'local' ? (
                <>
                  Answers are saved as <Link to={applyPath(project.slug)}>pending</Link> and change nothing until you
                  apply them.
                </>
              ) : (
                'Your answers are votes. Curators turn votes into decisions.'
              )}
            </p>
          </div>

          {rv.tracks.map((t) => (
            <section key={t.id} className="stack">
              <div>
                <h2 className="m-0">
                  {t.title} <span className="muted text-base font-normal">({t.total} items)</span>
                </h2>
                {t.blurb ? <p className="muted text-sm m-0">{t.blurb}</p> : null}
              </div>
              <div className="type-grid">
                {t.types.map((ty) => {
                  const counts = mode === 'hosted' ? hostedCounts(`${t.id}/${ty.id}`) : null;
                  return (
                    <Link key={ty.id} to={typePath(project.slug, t.id, ty.id)}
                      className={`type-card${ty.count === 0 ? ' type-card--empty' : ''}`}>
                      <div className="type-card__head">
                        <h3 className="m-0">{ty.title}</h3>
                        <span className="type-card__count">{ty.count}</span>
                      </div>
                      {ty.question ? <p className="type-card__question">{ty.question}</p> : null}
                      {ty.help ? <p className="type-card__help">{ty.help}</p> : null}
                      {ty.answers ? <p className="type-card__answers">{ty.answers}</p> : null}
                      {counts ? (
                        <p className="type-card__help">
                          {counts.mine} you have answered
                          {can.decide ? ` · ${counts.waiting} with votes waiting for a decision` : ''}
                        </p>
                      ) : null}
                    </Link>
                  );
                })}
              </div>
            </section>
          ))}

          <p className="muted text-sm">
            An item can sit under more than one kind of work. Want everything in one ranked list, with filters?{' '}
            <Link to={allPath(project.slug)}>All items</Link>.
          </p>
        </div>
      ) : null}
    </QueryState>
  );
}

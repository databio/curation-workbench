// Backend reads through the shared resource cache. Keys are
// [project, name, ...args], so `invalidate([project, 'decisions'])` hits one
// kind of data for one project.
import { useCallback } from 'react';
import { notifyDecision, useBackend } from '../api/backend';
import { useInvalidateResources, useResource } from './useResource';

export function useReview() {
  const b = useBackend();
  return useResource([b.project.slug, 'review'], () => b.review());
}

export function useReviewType(track: string, type: string) {
  const b = useBackend();
  return useResource([b.project.slug, 'reviewType', track, type], () => b.reviewType(track, type));
}

export function useSubjectIndex() {
  const b = useBackend();
  return useResource([b.project.slug, 'subject_index'], () => b.subjectIndex(), { staleTime: 60_000 });
}

export function useSubject(sid: string | undefined) {
  const b = useBackend();
  return useResource([b.project.slug, 'subject', sid ?? ''], () => b.subject(sid!), { enabled: !!sid });
}

export function useVerdicts() {
  const b = useBackend();
  return useResource([b.project.slug, 'verdicts'], () => b.verdicts(), { staleTime: 300_000 });
}

export function useFindings() {
  const b = useBackend();
  return useResource([b.project.slug, 'findings'], () => b.findings());
}

export function useHistory() {
  const b = useBackend();
  return useResource([b.project.slug, 'history'], () => b.history());
}

export function usePending() {
  const b = useBackend();
  return useResource([b.project.slug, 'pending'], () => b.pending());
}

export function useDecisions() {
  const b = useBackend();
  return useResource([b.project.slug, 'decisions'], () => b.decisions());
}

export function useQuestions(reviewType?: string) {
  const b = useBackend();
  return useResource([b.project.slug, 'questions', reviewType ?? ''], () => b.questions!(reviewType), {
    enabled: !!b.questions,
  });
}

/**
 * Call after any answer, undo, decide, resolve or withdraw: refreshes the
 * live store, questions and everything derived from them, and fires the
 * in-app decision event.
 */
export function useChanged(): () => void {
  const b = useBackend();
  const invalidate = useInvalidateResources();
  return useCallback(() => {
    for (const name of ['decisions', 'questions', 'subject', 'subject_index', 'pending', 'history']) {
      invalidate([b.project.slug, name]);
    }
    notifyDecision();
  }, [b, invalidate]);
}

/** Refetch the built lists too (after an apply). */
export function useRebuilt(): () => void {
  const b = useBackend();
  const invalidate = useInvalidateResources();
  return useCallback(() => invalidate([b.project.slug]), [b, invalidate]);
}

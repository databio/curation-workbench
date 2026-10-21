import React from 'react';

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error) return err.message || fallback;
  return typeof err === 'string' ? err : fallback;
}

interface QueryStateProps {
  isPending: boolean;
  error?: unknown;
  /** Shows a Retry button on error. */
  onRetry?: () => void;
  isEmpty?: boolean;
  empty?: React.ReactNode;
  children: React.ReactNode;
}

/** Loading / error with Retry / empty, else the children. Ported from Deadliner via Pigeon. */
export function QueryState({ isPending, error, onRetry, isEmpty, empty = 'Nothing here yet.', children }: QueryStateProps) {
  if (error) {
    return (
      <div className="query-state" role="alert">
        <p>{errorMessage(error, 'Something went wrong.')}</p>
        {onRetry ? (
          <button type="button" className="btn" onClick={onRetry}>
            Retry
          </button>
        ) : null}
      </div>
    );
  }
  if (isPending) {
    return (
      <div className="query-state" role="status">
        Loading...
      </div>
    );
  }
  if (isEmpty) return <div className="query-state">{empty}</div>;
  return <>{children}</>;
}

import React, { Fragment, useEffect, useRef, useState } from 'react';

interface LazyListProps<T> {
  items: T[];
  render: (item: T, index: number) => React.ReactNode;
  keyOf: (item: T) => string;
  /** Rows added each time the end of the list comes into view. */
  pageSize?: number;
  /** Rendering resets to the first page when this changes (filters, search). */
  resetKey?: unknown;
  as?: 'ol' | 'ul' | 'tbody';
  className?: string;
  /** For `as="tbody"`: the table's column count (the sentinel row spans them). */
  columns?: number;
}

/**
 * Renders the first `pageSize` items and appends `pageSize` more each time a
 * sentinel after the last row enters the viewport (an IntersectionObserver),
 * with a "Show more" button as the fallback. Rows should be memoized
 * components (ReviewItem is) so appending a page re-renders only the new ones.
 */
export function LazyList<T>({ items, render, keyOf, pageSize = 40, resetKey, as = 'ol', className, columns = 1 }: LazyListProps<T>) {
  const [count, setCount] = useState(pageSize);
  const sentinel = useRef<HTMLElement | null>(null);

  useEffect(() => setCount(pageSize), [resetKey, pageSize]);

  const more = count < items.length;
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !more || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setCount((c) => c + pageSize);
    });
    io.observe(el);
    return () => io.disconnect();
  }, [more, pageSize, count]);

  const rows = items.slice(0, count).map((it, i) => <Fragment key={keyOf(it)}>{render(it, i)}</Fragment>);
  const button = (
    <button type="button" className="btn btn--sm" onClick={() => setCount((c) => c + pageSize)}>
      Show more ({items.length - count} left)
    </button>
  );
  const setRef = (el: HTMLElement | null) => {
    sentinel.current = el;
  };
  if (as === 'tbody') {
    return (
      <tbody className={className}>
        {rows}
        {more ? (
          <tr ref={setRef} className="lazy-list__sentinel" data-testid="lazy-sentinel">
            <td colSpan={columns}>{button}</td>
          </tr>
        ) : null}
      </tbody>
    );
  }
  const Tag = as;
  return (
    <Tag className={className}>
      {rows}
      {more ? (
        <li ref={setRef} className="lazy-list__sentinel" data-testid="lazy-sentinel">
          {button}
        </li>
      ) : null}
    </Tag>
  );
}

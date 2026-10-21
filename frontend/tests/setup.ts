import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// @testing-library/react's auto-cleanup only self-registers when it detects
// Jest's global `afterEach`; this project does not turn on vitest's
// `globals`, so it must be wired up explicitly or DOM/state leaks between
// tests in the same file.
afterEach(() => {
  cleanup();
});

// React 18/19 act() environment flag, read by the testing-library adapters.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
  // @ts-expect-error -- tests replace global.fetch with a stub; restore it.
  if (globalThis.fetch && globalThis.fetch.mockRestore) {
    // @ts-expect-error -- vitest mock cleanup
    globalThis.fetch.mockRestore();
  }
});

import './io';

beforeEach(() => {
  try {
    sessionStorage.clear();
  } catch {
    // ignore
  }
});

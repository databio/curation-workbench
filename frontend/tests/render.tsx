import { render } from '@testing-library/react';
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router';
import { BackendContext, type Backend } from '../src/api/backend';
import { createResourceCache, NO_RETRY } from '../src/services/resourceCache';
import { ResourceCacheContext } from '../src/services/resourceCacheContext';
import { ToastProvider } from '../src/components/Toast';
import { projectChildren } from '../src/router';

/** Render the app's project routes at `path` with `backend`. */
export function renderAt(path: string, backend: Backend) {
  const cache = createResourceCache({ staleTime: 0, retry: NO_RETRY });
  const router = createMemoryRouter([{ path: '/p/:project', element: <Outlet />, children: projectChildren }], {
    initialEntries: [path],
  });
  return render(
    <ToastProvider>
      <ResourceCacheContext.Provider value={cache}>
        <BackendContext.Provider value={backend}>
          <RouterProvider router={router} />
        </BackendContext.Provider>
      </ResourceCacheContext.Provider>
    </ToastProvider>,
  );
}

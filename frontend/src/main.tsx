// Fetch /api/config first, then mount the local app (one project, the
// loopback daemon) or the hosted app (snapgate sign-in, many projects).
import React from 'react';
import ReactDOM from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router';
import { BackendContext } from './api/backend';
import { createLocalBackend, type LocalConfig } from './api/local';
import { createAppCache } from './app/cache';
import { ToastProvider } from './components/Toast';
import { hostedRoutes } from './hosted';
import { localRoutes } from './router';
import { ResourceCacheContext } from './services/resourceCacheContext';
import { SnapgateProvider } from './snapgate';
import './snapgate/snapgate.css';
import './styles/main.css';

async function start() {
  const root = ReactDOM.createRoot(document.getElementById('root')!);
  let config: { mode: string } & Partial<LocalConfig>;
  try {
    const res = await fetch('/api/config', { headers: { Accept: 'application/json' } });
    config = await res.json();
  } catch {
    root.render(<p className="query-state">Cannot reach the server (GET /api/config failed).</p>);
    return;
  }
  const cache = createAppCache();
  if (config.mode === 'local') {
    const backend = createLocalBackend(config as LocalConfig);
    const router = createBrowserRouter(localRoutes(backend.project.slug));
    root.render(
      <React.StrictMode>
        <ToastProvider>
          <ResourceCacheContext.Provider value={cache}>
            <BackendContext.Provider value={backend}>
              <RouterProvider router={router} />
            </BackendContext.Provider>
          </ResourceCacheContext.Provider>
        </ToastProvider>
      </React.StrictMode>,
    );
    return;
  }
  const router = createBrowserRouter(hostedRoutes);
  root.render(
    <React.StrictMode>
      {/* apiUrl="" because snapgate is mounted at the app root (/auth/*), same origin. */}
      <SnapgateProvider apiUrl="" userAdmin>
        <ToastProvider>
          <ResourceCacheContext.Provider value={cache}>
            <RouterProvider router={router} />
          </ResourceCacheContext.Provider>
        </ToastProvider>
      </SnapgateProvider>
    </React.StrictMode>,
  );
}

void start();

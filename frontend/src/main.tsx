// Mount the app: snapgate sign-in, then the project picker and one Backend
// per project (hosted.tsx).
import React from 'react';
import ReactDOM from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router';
import { createAppCache } from './app/cache';
import { ToastProvider } from './components/Toast';
import { hostedRoutes } from './hosted';
import { ResourceCacheContext } from './services/resourceCacheContext';
import { SnapgateProvider } from './snapgate';
import './snapgate/snapgate.css';
import './styles/main.css';

const root = ReactDOM.createRoot(document.getElementById('root')!);
const router = createBrowserRouter(hostedRoutes);
root.render(
  <React.StrictMode>
    {/* apiUrl="" because snapgate is mounted at the app root (/auth/*), same origin. */}
    <SnapgateProvider apiUrl="" userAdmin>
      <ToastProvider>
        <ResourceCacheContext.Provider value={createAppCache()}>
          <RouterProvider router={router} />
        </ResourceCacheContext.Provider>
      </ToastProvider>
    </SnapgateProvider>
  </React.StrictMode>,
);

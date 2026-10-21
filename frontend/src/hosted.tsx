// Hosted mode's routes: snapgate sign-in, the project picker, accounts, and
// /p/:project behind sign-in with a Backend loaded per project.
import React, { useEffect, useState } from 'react';
import { Navigate, Outlet, useParams, type RouteObject } from 'react-router';
import { BackendContext, type Backend } from './api/backend';
import { loadHostedBackend } from './api/hosted';
import { Layout } from './components/Layout';
import { QueryState } from './components/QueryState';
import NotFound from './pages/NotFound';
import Accounts from './pages/hosted/Accounts';
import Login from './pages/hosted/Login';
import Projects from './pages/hosted/Projects';
import { HostedSessionProvider } from './pages/hosted/session';
import { projectChildren } from './router';
import { PendingApproval, ProtectedRoute, useSnapgate } from './snapgate';

/** Signed in and approved (snapgate), with the person's `/api/v1/me` loaded. */
export function Authed({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute>
      <ApprovedOnly>
        <HostedSessionProvider>{children}</HostedSessionProvider>
      </ApprovedOnly>
    </ProtectedRoute>
  );
}

function ApprovedOnly({ children }: { children: React.ReactNode }) {
  const { user } = useSnapgate();
  if (user && user.status !== 'approved') return <Navigate to="/pending-approval" replace />;
  return <>{children}</>;
}

/** Loads the project's Backend; a project you are not on is not found (the server says 404). */
function ProjectScope() {
  const { project = '' } = useParams();
  const [state, setState] = useState<{ backend: Backend | null; error: unknown }>({ backend: null, error: null });
  useEffect(() => {
    let live = true;
    setState({ backend: null, error: null });
    loadHostedBackend(project)
      .then((backend) => live && setState({ backend, error: null }))
      .catch((error) => live && setState({ backend: null, error }));
    return () => {
      live = false;
    };
  }, [project]);
  if (state.error) return <Layout><NotFound /></Layout>;
  return (
    <QueryState isPending={!state.backend}>
      <BackendContext.Provider value={state.backend}>
        <Layout>
          <Outlet />
        </Layout>
      </BackendContext.Provider>
    </QueryState>
  );
}

export const hostedRoutes: RouteObject[] = [
  { path: '/login', element: <Login /> },
  { path: '/pending-approval', element: <PendingApproval approvedRedirect="/" loginRedirect="/login" /> },
  { path: '/', element: <Authed><Layout><Projects /></Layout></Authed> },
  { path: '/admin/accounts', element: <Authed><Layout><Accounts /></Layout></Authed> },
  { path: '/p/:project', element: <Authed><ProjectScope /></Authed>, children: projectChildren },
  { path: '*', element: <Layout><NotFound /></Layout> },
];

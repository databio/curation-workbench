// The route table, shared by both modes (one bundle, mode chosen at run time
// from GET /api/config):
//
// /                                   local: -> /p/<project>; hosted: project picker
// /login, /pending-approval           hosted only (snapgate)
// /p/:project                         Review home
// /p/:project/review/:track/:type     Review type page
// /p/:project/subject/:slug           Subject page
// /p/:project/apply                   Apply (pending decisions)
// /p/:project/more                    More index
// /p/:project/history                 History (decision log)
// /p/:project/all                     All items (ranked queue, filters)
// /p/:project/findings                Findings report
// /p/:project/team                    Team admin (can.adminTeam)
// /admin/accounts                     snapgate AdminUsersPage (hosted superadmin)
// *                                   NotFound
//
// Local mode provides the one project's Backend above the router; hosted
// mode loads one per /p/:project (ProjectScope) behind sign-in (Authed).
import { Navigate, Outlet, type RouteObject } from 'react-router';
import { Layout } from './components/Layout';
import AllItems from './pages/AllItems';
import Apply from './pages/Apply';
import Findings from './pages/Findings';
import History from './pages/History';
import More from './pages/More';
import NotFound from './pages/NotFound';
import ReviewHome from './pages/ReviewHome';
import ReviewType from './pages/ReviewType';
import Subject from './pages/Subject';
import Team from './pages/Team';

export const projectChildren: RouteObject[] = [
  { index: true, element: <ReviewHome /> },
  { path: 'review/:track/:type', element: <ReviewType /> },
  { path: 'subject/:slug', element: <Subject /> },
  { path: 'apply', element: <Apply /> },
  { path: 'more', element: <More /> },
  { path: 'history', element: <History /> },
  { path: 'all', element: <AllItems /> },
  { path: 'findings', element: <Findings /> },
  { path: 'team', element: <Team /> },
  { path: '*', element: <NotFound /> },
];

/** Local mode: one project; anything under another project slug is not found. */
export function localRoutes(slug: string): RouteObject[] {
  return [
    { path: '/', element: <Navigate to={`/p/${encodeURIComponent(slug)}`} replace /> },
    {
      path: `/p/${slug}`,
      element: (
        <Layout>
          <Outlet />
        </Layout>
      ),
      children: projectChildren,
    },
    { path: '*', element: <Layout><NotFound /></Layout> },
  ];
}

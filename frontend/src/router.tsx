// The project routes (hosted.tsx adds sign-in, the project picker and
// accounts around them):
//
// /                                   project picker
// /login, /pending-approval           snapgate
// /p/:project                         Review home
// /p/:project/review/:track/:type     Review type page
// /p/:project/subject/:slug           Subject page
// /p/:project/approved                Approved, waiting to reach the repository
// /p/:project/more                    More index
// /p/:project/history                 History (decision log)
// /p/:project/all                     All items (ranked queue, filters)
// /p/:project/findings                Findings report
// /p/:project/team                    Team admin (can.adminTeam)
// /admin/accounts                     snapgate AdminUsersPage (superadmin)
// *                                   NotFound
//
// hosted.tsx loads a Backend per /p/:project (ProjectScope) behind sign-in
// (Authed).
import type { RouteObject } from 'react-router';
import AllItems from './pages/AllItems';
import Approved from './pages/Approved';
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
  { path: 'approved', element: <Approved /> },
  { path: 'more', element: <More /> },
  { path: 'history', element: <History /> },
  { path: 'all', element: <AllItems /> },
  { path: 'findings', element: <Findings /> },
  { path: 'team', element: <Team /> },
  { path: '*', element: <NotFound /> },
];

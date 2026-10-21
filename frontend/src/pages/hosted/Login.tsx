// Hosted sign-in (snapgate, Prosopia accounts). Calls snapgate's `login`
// directly so a deep link survives the sign-in round trip.
import { Navigate, useSearchParams } from 'react-router';
import { clearSessionLost, sessionWasLost, useSnapgate } from '../../snapgate';

export function safeReturnTo(raw: string | null): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return '/';
  return raw;
}

export default function Login() {
  const { user, loading, login } = useSnapgate();
  const [params] = useSearchParams();
  const returnTo = safeReturnTo(params.get('returnTo'));
  if (loading) return null;
  if (user) return <Navigate to={returnTo} replace />;
  return (
    <div className="login">
      <h1 className="m-0">Curation review</h1>
      {sessionWasLost() ? <div className="banner">Your session expired. Sign in again.</div> : null}
      {params.get('error') ? <div className="banner banner--error">Sign-in failed. Please try again.</div> : null}
      <p className="muted">
        Review a schema with your team: each answer is a vote, and curators make the decisions.
      </p>
      <p className="muted">Sign in with Prosopia. New accounts wait for approval.</p>
      <button type="button" className="btn btn--primary" onClick={() => {
        clearSessionLost();
        login(undefined, window.location.origin + returnTo);
      }}>
        Sign in
      </button>
    </div>
  );
}

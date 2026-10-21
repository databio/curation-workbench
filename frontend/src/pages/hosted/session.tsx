// Hosted mode: the signed-in person (`/api/v1/me`), loaded once per session.
import { createContext, useContext, useEffect, useState } from 'react';
import { createHostedClient, type Me } from '../../api/hosted';

interface HostedSession {
  me: Me | null;
  error: unknown;
}

const Ctx = createContext<HostedSession>({ me: null, error: null });

export function HostedSessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<HostedSession>({ me: null, error: null });
  useEffect(() => {
    let live = true;
    createHostedClient().request<Me>('GET', '/api/v1/me')
      .then((me) => live && setState({ me, error: null }))
      .catch((error) => live && setState({ me: null, error }));
    return () => {
      live = false;
    };
  }, []);
  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

export const useHostedSession = () => useContext(Ctx);

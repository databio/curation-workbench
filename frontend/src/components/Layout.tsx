import React, { useContext } from 'react';
import { BackendContext } from '../api/backend';
import { NavBar, PlainNav } from './NavBar';

/** App chrome: the project nav inside a project, a plain bar outside one (hosted picker, accounts). */
export function Layout({ children }: { children: React.ReactNode }) {
  const backend = useContext(BackendContext);
  return (
    <div className="layout">
      {backend ? <NavBar /> : <PlainNav />}
      <main className="layout__main">{children}</main>
    </div>
  );
}

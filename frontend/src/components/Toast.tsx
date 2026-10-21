import React, { createContext, useCallback, useContext, useRef, useState } from 'react';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

interface ToastItem {
  id: number;
  message: string;
  kind: 'success' | 'error';
  action?: ToastAction;
}

interface ToastContextValue {
  showToast: (message: string, kind?: 'success' | 'error', action?: ToastAction) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(0);

  const dropToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback(
    (message: string, kind: 'success' | 'error' = 'success', action?: ToastAction) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, message, kind, action }]);
      // a toast with an action (Undo) stays up longer so there is time to click it
      setTimeout(() => dropToast(id), action ? 8000 : 4000);
    },
    [dropToast],
  );

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.kind === 'error' ? ' toast--error' : ''}`}>
            {t.message}
            {t.action ? (
              <button
                type="button"
                className="btn btn--sm toast__action"
                onClick={() => {
                  dropToast(t.id);
                  t.action?.onClick();
                }}
              >
                {t.action.label}
              </button>
            ) : null}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within a ToastProvider');
  return ctx;
}

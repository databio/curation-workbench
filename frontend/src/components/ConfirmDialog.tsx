import { useEffect, useState, type ReactNode } from 'react';
import { BaseModal } from './common/BaseModal';

export interface ConfirmTextarea {
  label: string;
  maxLength: number;
  placeholder?: string;
}

interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Red confirm button. */
  destructive?: boolean;
  /** An optional text box (decline message, delivery note); its value goes to `onConfirm`. */
  textarea?: ConfirmTextarea;
  /** Gets the textarea value ('' without one). The button is disabled while it runs. */
  onConfirm: (value: string) => unknown;
  onCancel: () => void;
}

export function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  textarea,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [value, setValue] = useState('');
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (isOpen) setValue('');
  }, [isOpen]);

  const tooLong = !!textarea && value.length > textarea.maxLength;

  const confirm = async () => {
    if (running || tooLong) return;
    setRunning(true);
    try {
      await onConfirm(value);
    } finally {
      setRunning(false);
    }
  };

  return (
    <BaseModal isOpen={isOpen} onClose={onCancel} title={title} size="sm">
      <div className="stack">
        <div>{message}</div>
        {textarea ? (
          <div className="form">
            <div className="form__row">
              <label className="form__label" htmlFor="confirm-dialog-text">
                {textarea.label}
              </label>
              <div className="form__control">
                <textarea
                  id="confirm-dialog-text"
                  className="form__input"
                  rows={3}
                  value={value}
                  maxLength={textarea.maxLength}
                  placeholder={textarea.placeholder}
                  aria-invalid={tooLong || undefined}
                  aria-describedby={tooLong ? 'confirm-dialog-count confirm-dialog-error' : 'confirm-dialog-count'}
                  onChange={(e) => setValue(e.target.value)}
                />
                <span id="confirm-dialog-count" className="form__hint">
                  {value.length} / {textarea.maxLength}
                </span>
                {tooLong ? (
                  <span id="confirm-dialog-error" className="form__error">
                    Too long.
                  </span>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}
      </div>
      <BaseModal.Footer>
        <button type="button" className="btn" onClick={onCancel}>
          {cancelLabel}
        </button>
        <button
          type="button"
          className={destructive ? 'btn btn--danger' : 'btn btn--primary'}
          onClick={confirm}
          disabled={running || tooLong}
        >
          {confirmLabel}
        </button>
      </BaseModal.Footer>
    </BaseModal>
  );
}

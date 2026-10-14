import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react';
import { cx } from '@/lib/cx';
import styles from './Field.module.css';

/** Field'ın denetim öğesine (input/select/textarea) verdiği erişilebilirlik özellikleri */
export interface ControlProps {
  id: string;
  className: string;
  required?: boolean;
  'aria-invalid'?: true;
  'aria-describedby'?: string;
}

export interface FieldProps {
  label: ReactNode;
  /** Yardım metni (etiketin altında, denetime aria-describedby ile bağlı) */
  hint?: ReactNode;
  /** Hata metni; verilince denetim aria-invalid olur */
  error?: string | null | undefined;
  required?: boolean;
  id?: string;
  className?: string;
  children: (control: ControlProps) => ReactNode;
}

/**
 * Etiket + yardım + hata sarmalayıcısı. Denetimi render prop ile alır:
 *   <Field label="Başlık" error={e}>{(p) => <input {...p} />}</Field>
 */
export function Field({ label, hint, error, required, id, className, children }: FieldProps) {
  const autoId = useId();
  const controlId = id ?? `f${autoId}`;
  const hintId = hint ? `${controlId}-hint` : undefined;
  const errorId = error ? `${controlId}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;
  return (
    <div className={cx(styles.field, className)}>
      <label className={styles.label} htmlFor={controlId}>
        {label}
        {required && (
          <span className={styles.required} aria-hidden="true">
            *
          </span>
        )}
      </label>
      {hint && (
        <p className={styles.hint} id={hintId}>
          {hint}
        </p>
      )}
      {children({
        id: controlId,
        className: styles.control ?? '',
        ...(required ? { required: true } : {}),
        ...(error ? { 'aria-invalid': true } : {}),
        ...(describedBy ? { 'aria-describedby': describedBy } : {}),
      })}
      {error && (
        <p className={styles.error} id={errorId}>
          {error}
        </p>
      )}
    </div>
  );
}

type FieldOwnProps = Pick<FieldProps, 'label' | 'hint' | 'error' | 'required' | 'className'>;

export type TextInputProps = FieldOwnProps &
  Omit<InputHTMLAttributes<HTMLInputElement>, 'className' | 'required'>;

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(function TextInput(
  { label, hint, error, required, className, id, ...input },
  ref,
) {
  return (
    <Field
      label={label}
      hint={hint}
      error={error}
      required={required}
      className={className}
      id={id}
    >
      {(p) => <input ref={ref} type="text" {...input} {...p} />}
    </Field>
  );
});

export type TextAreaProps = FieldOwnProps &
  Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'className' | 'required'>;

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { label, hint, error, required, className, id, ...textarea },
  ref,
) {
  return (
    <Field
      label={label}
      hint={hint}
      error={error}
      required={required}
      className={className}
      id={id}
    >
      {(p) => <textarea ref={ref} {...textarea} {...p} />}
    </Field>
  );
});

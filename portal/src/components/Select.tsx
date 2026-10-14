import { forwardRef, type SelectHTMLAttributes } from 'react';
import { Field, type FieldProps } from './Field';

export interface SelectOption<V extends string = string> {
  value: V;
  label: string;
  disabled?: boolean;
}

export interface SelectProps<V extends string = string>
  extends Pick<FieldProps, 'label' | 'hint' | 'error' | 'required' | 'className'>,
    Omit<SelectHTMLAttributes<HTMLSelectElement>, 'className' | 'required'> {
  options: readonly SelectOption<V>[];
  /** Boş seçenek metni (ör. "Seçiniz"); verilirse value="" ile en başa eklenir */
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, hint, error, required, className, id, options, placeholder, ...select },
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
      {(p) => (
        <select ref={ref} {...select} {...p}>
          {placeholder !== undefined && <option value="">{placeholder}</option>}
          {options.map((o) => (
            <option key={o.value} value={o.value} disabled={o.disabled}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
});

/** Etiket sözlüğünden (lib/labels) seçenek listesi üretir */
export function optionsFrom<V extends string>(labels: Record<V, string>): SelectOption<V>[] {
  return (Object.keys(labels) as V[]).map((value) => ({ value, label: labels[value] }));
}

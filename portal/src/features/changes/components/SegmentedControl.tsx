import { useId } from 'react';
import styles from '../Changes.module.css';

export interface SegmentedControlProps<V extends string> {
  legend: string;
  value: V;
  options: readonly { value: V; label: string }[];
  onChange: (value: V) => void;
}

/** Radyo grubu olarak görünüm seçici (klavye: ok tuşları yerel radyo davranışı) */
export function SegmentedControl<V extends string>({
  legend,
  value,
  options,
  onChange,
}: SegmentedControlProps<V>) {
  const name = useId();
  return (
    <fieldset className={styles.segmented}>
      <legend className="visually-hidden">{legend}</legend>
      {options.map((o) => (
        <label key={o.value} className={styles.segment} data-checked={o.value === value}>
          <input
            type="radio"
            name={name}
            value={o.value}
            checked={o.value === value}
            onChange={() => {
              onChange(o.value);
            }}
          />
          {o.label}
        </label>
      ))}
    </fieldset>
  );
}

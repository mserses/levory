import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { IMPACT_LABELS } from '@/lib/labels';
import { Field, TextArea, TextInput } from './Field';
import { Select, optionsFrom } from './Select';

describe('Field', () => {
  it('etiket, yardım ve hata metnini denetime bağlar', () => {
    render(<TextInput label="Başlık" hint="Kısa bir özet" error="Başlık zorunlu" required />);
    const input = screen.getByRole('textbox', { name: /Başlık/ });
    expect(input).toBeRequired();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription('Kısa bir özet Başlık zorunlu');
  });

  it('hata yokken aria-invalid yok', () => {
    render(<TextArea label="Açıklama" />);
    const area = screen.getByRole('textbox', { name: 'Açıklama' });
    expect(area).not.toHaveAttribute('aria-invalid');
    expect(area.tagName).toBe('TEXTAREA');
  });

  it('render prop ile özel denetim', () => {
    render(<Field label="Tarih">{(p) => <input type="date" {...p} />}</Field>);
    expect(screen.getByLabelText('Tarih')).toHaveAttribute('type', 'date');
  });
});

describe('Select', () => {
  it('seçenekler ve placeholder', async () => {
    const onChange = vi.fn();
    render(
      <Select
        label="Etki"
        placeholder="Seçiniz"
        options={optionsFrom(IMPACT_LABELS)}
        onChange={onChange}
        defaultValue=""
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Etki' });
    expect(screen.getAllByRole('option')).toHaveLength(4);
    await userEvent.selectOptions(select, 'medium');
    expect(onChange).toHaveBeenCalled();
    expect(select).toHaveValue('medium');
  });
});

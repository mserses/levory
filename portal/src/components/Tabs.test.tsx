import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { Tabs } from './Tabs';

const ITEMS = [
  { key: 'a', label: 'Birinci' },
  { key: 'b', label: 'İkinci' },
  { key: 'c', label: 'Üçüncü' },
] as const;

function Harness() {
  const [active, setActive] = useState<'a' | 'b' | 'c'>('a');
  return (
    <Tabs label="Bölümler" items={ITEMS} active={active} onChange={setActive}>
      <p>İçerik {active}</p>
    </Tabs>
  );
}

describe('Tabs', () => {
  it('WAI-ARIA sekme deseni: seçili sekme, panel bağı, ok/Home/End ile gezinme', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const first = screen.getByRole('tab', { name: 'Birinci' });
    expect(screen.getByRole('tablist', { name: 'Bölümler' })).toBeInTheDocument();
    expect(first).toHaveAttribute('aria-selected', 'true');
    expect(first).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', { name: 'İkinci' })).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('tabpanel', { name: 'Birinci' })).toHaveTextContent('İçerik a');

    first.focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'İkinci' })).toHaveFocus();
    expect(screen.getByRole('tabpanel')).toHaveTextContent('İçerik b');
    await user.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'Üçüncü' })).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Birinci' })).toHaveFocus();
    await user.keyboard('{ArrowLeft}{Home}');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('İçerik a');
    await user.click(screen.getByRole('tab', { name: 'Üçüncü' }));
    expect(screen.getByRole('tabpanel')).toHaveTextContent('İçerik c');
  });
});

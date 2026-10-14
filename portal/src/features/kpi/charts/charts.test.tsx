import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BarChart } from './BarChart';
import { LineChart } from './LineChart';

describe('LineChart', () => {
  const props = {
    title: 'MTTR (önceliğe göre)',
    description: 'Kova başına ortalama etkin çözüm süresi',
    unit: 'hours' as const,
    xLabels: ['1 Eki', '6 Eki', '11 Eki'],
    series: [
      { key: 'P1', label: 'P1 · Kritik', values: [2, null, 1.5], colorIndex: 0 },
      { key: 'P3', label: 'P3 · Orta', values: [9, 7, 6], colorIndex: 2 },
    ],
  };

  it('erişilebilir: figür adı, metin özeti, açıklama ve veri tablosu', () => {
    render(<LineChart {...props} />);
    const fig = screen.getByRole('figure', { name: 'MTTR (önceliğe göre)' });
    const img = within(fig).getByRole('img');
    expect(img.getAttribute('aria-label')).toContain('P3 · Orta: 9 sa → 6 sa');
    const legend = within(fig).getByRole('list', { name: 'Seriler' });
    expect(within(legend).getAllByRole('listitem')).toHaveLength(2);
    const table = within(fig).getByRole('table', { name: 'MTTR (önceliğe göre) — veri tablosu' });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(4);
    expect(
      within(rows[0] as HTMLElement)
        .getAllByRole('columnheader')
        .map((c) => c.textContent),
    ).toEqual(['Dönem', 'P1 · Kritik', 'P3 · Orta']);
    expect(
      within(rows[2] as HTMLElement)
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['—', '7 sa']);
  });

  it('üzerine gelince dönem değerlerini gösterir', () => {
    render(<LineChart {...props} />);
    const target = screen.getByTestId('hover-1');
    fireEvent.mouseEnter(target);
    const tip = screen.getByTestId('chart-tooltip');
    expect(tip).toHaveTextContent('6 Eki');
    expect(tip).toHaveTextContent('P3 · Orta7 sa');
    fireEvent.mouseLeave(target);
    expect(screen.queryByTestId('chart-tooltip')).toBeNull();
  });

  it('veri yoksa boş durum metni', () => {
    render(<LineChart {...props} series={[]} />);
    expect(screen.getByText('Bu dönemde gösterilecek veri yok.')).toBeInTheDocument();
  });
});

describe('BarChart', () => {
  it('çubuklar + değer etiketleri + veri tablosu', () => {
    render(
      <BarChart
        title="Birikim yaşlanması"
        unit="count"
        items={[
          { label: '1 günden az', value: 4 },
          { label: '30 günden fazla', value: 1 },
        ]}
      />,
    );
    const fig = screen.getByRole('figure', { name: 'Birikim yaşlanması' });
    expect(within(fig).getByRole('img').getAttribute('aria-label')).toBe(
      'Birikim yaşlanması: 1 günden az 4, 30 günden fazla 1',
    );
    const table = within(fig).getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(3);
  });
});

import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { ROUTER_FUTURE } from '@/app/routerFuture';
import { makeDecision } from '@/test/fixtures';
import { AIDecisionSummary } from './AIDecisionSummary';

describe('AIDecisionSummary', () => {
  it('sonuç, güven, gerekçe ve KB kaynaklarını gösterir', () => {
    const decision = makeDecision({
      outcome: 'needs_approval',
      confidence: 0.64,
      rationale: 'P2 -> P1 yükseltme önerisi.\nBirden çok birim etkileniyor.',
      sources: ['kb-1', 'kb-2'],
    });
    render(
      <MemoryRouter future={ROUTER_FUTURE}>
        <AIDecisionSummary decision={decision} actions={<button type="button">Onayla</button>} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('article', { name: 'Yapay zekâ önerisi' })).toBeInTheDocument();
    expect(screen.getByText('Onay bekliyor')).toBeInTheDocument();
    expect(screen.getByRole('meter', { name: 'Güven' })).toHaveAttribute('aria-valuenow', '64');
    expect(screen.getByText(/Birden çok birim etkileniyor/)).toHaveClass('prewrap');
    expect(screen.getByRole('link', { name: 'kb-1' })).toHaveAttribute(
      'href',
      '/bilgi-bankasi/kb-1',
    );
    expect(screen.getByRole('button', { name: 'Onayla' })).toBeInTheDocument();
  });

  it('kaynak yoksa belirtir; yapay zekâ metni HTML olarak işlenmez', () => {
    render(
      <MemoryRouter future={ROUTER_FUTURE}>
        <AIDecisionSummary decision={makeDecision({ rationale: '<b>kalın</b>', sources: [] })} />
      </MemoryRouter>,
    );
    expect(screen.getByText('Kaynak gösterilmedi.')).toBeInTheDocument();
    expect(screen.getByText('<b>kalın</b>')).toBeInTheDocument();
  });
});

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { makeIncident } from '@/test/fixtures';
import { Badge } from './Badge';
import { OutcomeBadge } from './OutcomeBadge';
import { PriorityBadge } from './PriorityBadge';
import { SlaBadge } from './SlaBadge';
import { StatusBadge } from './StatusBadge';

describe('rozetler', () => {
  it('Badge metni ve tonu', () => {
    render(<Badge tone="success">Tamam</Badge>);
    expect(screen.getByText('Tamam').className).toContain('success');
  });

  it('PriorityBadge tam ve kompakt', () => {
    const { rerender } = render(<PriorityBadge priority="P1" />);
    expect(screen.getByText('P1 · Kritik')).toBeInTheDocument();
    rerender(<PriorityBadge priority="P2" compact />);
    expect(screen.getByText('P2')).toBeInTheDocument();
    expect(screen.getByText('Öncelik P2 · Yüksek')).toHaveClass('visually-hidden');
  });

  it('StatusBadge bekleme nedeniyle', () => {
    render(<StatusBadge status="on_hold" onHoldReason="awaiting_vendor" />);
    const badge = screen.getByTitle('Tedarikçi bekleniyor (SLA durur)');
    expect(badge).toHaveTextContent('Beklemede: Tedarikçi bekleniyor (SLA durur)');
  });

  it('OutcomeBadge Türkçe etiket ve açıklama', () => {
    render(<OutcomeBadge outcome="needs_approval" />);
    expect(screen.getByText('Onay bekliyor')).toHaveAttribute(
      'title',
      expect.stringContaining('insan onayı'),
    );
  });

  it.each([
    ['2026-10-12T09:00:00Z', 'on_track', 'Yanıt: 3 sa kaldı'],
    ['2026-10-12T11:30:00Z', 'at_risk', 'Yanıt: 30 dk kaldı'],
    ['2026-10-12T12:45:00Z', 'breached', 'Yanıt SLA aşıldı · 45 dk'],
  ])('SlaBadge %s -> %s', (now, state, text) => {
    const incident = makeIncident({
      created_at: '2026-10-12T08:00:00Z',
      response_due_at: '2026-10-12T12:00:00Z',
      resolve_due_at: '2026-10-13T08:00:00Z',
    });
    render(<SlaBadge incident={incident} now={new Date(now)} />);
    const badge = screen.getByText(text);
    expect(badge).toHaveAttribute('data-sla-state', state);
    expect(badge).toHaveAttribute('title', 'Yanıt vadesi: 12.10.2026 15:00');
  });

  it('SlaBadge duraklatılmış', () => {
    render(
      <SlaBadge
        incident={makeIncident({ status: 'on_hold', sla_paused_at: '2026-10-12T09:00:00Z' })}
      />,
    );
    expect(screen.getByText('SLA duraklatıldı')).toBeInTheDocument();
  });
});

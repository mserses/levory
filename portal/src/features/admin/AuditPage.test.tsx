import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { AuditEntry } from '@/api/types';
import { apiUrl } from '@/test/handlers';
import { ADMIN, AGENT, renderApp, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import AuditPage from './AuditPage';

function entry(seq: number, overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    seq,
    occurred_at: '2026-10-12T09:00:00Z',
    actor: 'ayse',
    action: 'incident.update',
    entity_type: 'incident',
    entity_id: `inc-${String(seq)}`,
    details: { fields: ['status'] },
    prev_hash: 'a'.repeat(64),
    hash: `${String(seq)}${'b'.repeat(63)}`,
    ...overrides,
  };
}

/** Sunucu gibi davranır: en yeni önce, limit/offset */
function mockAudit(total: number) {
  const calls: URL[] = [];
  server.use(
    http.get(apiUrl('/audit'), ({ request }) => {
      const url = new URL(request.url);
      calls.push(url);
      const limit = Number(url.searchParams.get('limit'));
      const offset = Number(url.searchParams.get('offset'));
      const items: AuditEntry[] = [];
      for (let s = total - offset; s > Math.max(0, total - offset - limit); s--)
        items.push(entry(s));
      return HttpResponse.json({ total, limit, offset, items });
    }),
  );
  return calls;
}

const render = () => renderWithProviders(<AuditPage />, { user: ADMIN, route: '/yonetim/denetim' });

describe('AuditPage', () => {
  it('kayıtları en yeni önce, Türkçe işlem etiketleri ve ayrıntılarla listeler', async () => {
    server.use(
      http.get(apiUrl('/audit'), () =>
        HttpResponse.json({
          total: 2,
          limit: 25,
          offset: 0,
          items: [
            entry(2, {
              action: 'knowledge.publish',
              entity_type: 'knowledge_article',
              details: { note: '<b>kalın değil</b>' },
            }),
            entry(1, { action: 'custom.unknown', details: {} }),
          ],
        }),
      ),
    );
    const { container } = render();
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Denetim günlüğü' }),
    ).toBeInTheDocument();
    const rows = await screen.findAllByTestId('audit-row');
    const [first, second] = rows as [HTMLElement, HTMLElement];
    expect(within(first).getByText('#2')).toBeInTheDocument();
    expect(within(first).getByText('Makale yayınlandı')).toBeInTheDocument();
    expect(within(first).getByText('Makale')).toBeInTheDocument();
    expect(within(first).getByText('1 alan')).toBeInTheDocument();
    expect(within(first).getByText(/<b>kalın değil<\/b>/)).toBeInTheDocument();
    expect(container.querySelector('tbody b')).toBeNull();
    expect(within(first).getByTitle(`2${'b'.repeat(63)}`)).toHaveTextContent('2bbbbbbbbbbb…');
    // Bilinmeyen işlem ham kodla gösterilir
    expect(within(second).getAllByText('custom.unknown').length).toBeGreaterThan(0);
    expect(screen.getByText('Toplam 2 kayıt')).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Sayfalama' })).not.toBeInTheDocument();
  });

  it('sayfalar arasında gezinir (limit/offset)', async () => {
    const calls = mockAudit(60);
    const { user } = render();
    expect(await screen.findAllByTestId('audit-row')).toHaveLength(25);
    expect(calls[0]?.searchParams.get('limit')).toBe('25');
    expect(calls[0]?.searchParams.get('offset')).toBe('0');
    expect(screen.getByText('Sayfa 1 / 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '← Daha yeni' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Daha eski →' }));
    await user.click(await screen.findByRole('button', { name: 'Daha eski →' }));
    expect(await screen.findByText('Sayfa 3 / 3')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByTestId('audit-row')).toHaveLength(10);
    });
    expect(calls.at(-1)?.searchParams.get('offset')).toBe('50');
    expect(screen.getByText('#10')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Daha eski →' })).toBeDisabled();
  });

  it('zincir doğrulaması geçerli sonucu net gösterir', async () => {
    mockAudit(1);
    server.use(http.get(apiUrl('/audit/verify'), () => HttpResponse.json({ valid: true })));
    const { user } = render();
    await user.click(await screen.findByRole('button', { name: 'Zinciri doğrula' }));
    const status = screen.getByTestId('verify-status');
    expect(await within(status).findByText('Zincir geçerli')).toBeInTheDocument();
    expect(status).toHaveAttribute('role', 'status');
    expect(screen.getByRole('button', { name: 'Yeniden doğrula' })).toBeInTheDocument();
  });

  it('bozuk zinciri açıkça uyarır', async () => {
    mockAudit(1);
    server.use(http.get(apiUrl('/audit/verify'), () => HttpResponse.json({ valid: false })));
    const { user } = render();
    await user.click(await screen.findByRole('button', { name: 'Zinciri doğrula' }));
    expect(await screen.findByText('Zincir BOZUK')).toBeInTheDocument();
    expect(screen.getByText(/güvenlik ekibini bilgilendirin/)).toBeInTheDocument();
    expect(screen.queryByText('Zincir geçerli')).not.toBeInTheDocument();
  });

  it('doğrulama isteği başarısız olursa hata gösterir', async () => {
    mockAudit(1);
    server.use(
      http.get(apiUrl('/audit/verify'), () =>
        HttpResponse.json({ detail: 'Veritabanına ulaşılamadı' }, { status: 503 }),
      ),
    );
    const { user } = render();
    await user.click(await screen.findByRole('button', { name: 'Zinciri doğrula' }));
    expect(await screen.findByText('Doğrulama yapılamadı')).toBeInTheDocument();
    expect(screen.getByText('Veritabanına ulaşılamadı')).toBeInTheDocument();
  });

  it('liste hatası ve boş günlük', async () => {
    server.use(
      http.get(apiUrl('/audit'), () =>
        HttpResponse.json({ detail: 'Sunucu hatası' }, { status: 500 }),
      ),
    );
    const { user } = render();
    expect(await screen.findByText('Sunucu hatası')).toBeInTheDocument();
    mockAudit(0);
    await user.click(screen.getByRole('button', { name: 'Yeniden dene' }));
    expect(
      await screen.findByRole('heading', { name: 'Henüz denetim kaydı yok' }),
    ).toBeInTheDocument();
  });

  it('yönetici olmayan kullanıcı ekrana erişemez', async () => {
    renderApp({ user: AGENT, route: '/yonetim/denetim' });
    expect(
      await screen.findByRole('heading', { name: 'Bu sayfayı görüntüleme yetkiniz yok' }),
    ).toBeInTheDocument();
  });
});

import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { ConfigurationItem } from '@/api/types';
import { makeCI } from '@/features/changes/testUtils';
import { apiUrl } from '@/test/handlers';
import { AGENT, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import CmdbListPage from './CmdbListPage';

function mockList(respond: (url: URL) => ConfigurationItem[] = () => []) {
  const calls: URL[] = [];
  server.use(
    http.get(apiUrl('/cmdb/cis'), ({ request }) => {
      const url = new URL(request.url);
      calls.push(url);
      return HttpResponse.json(respond(url));
    }),
  );
  return calls;
}

describe('CmdbListPage (/cmdb)', () => {
  it('CI listesini Türkçe etiketlerle gösterir; ayrıntı bağlantısı', async () => {
    const ci = makeCI({
      name: 'erp-db-01',
      ci_class: 'database',
      environment: 'prod',
      criticality: 1,
      owner_group: 'dba',
      status: 'maintenance',
    });
    mockList(() => [ci]);
    renderWithProviders(<CmdbListPage />, { user: AGENT, route: '/cmdb' });
    expect(await screen.findByRole('heading', { level: 1, name: 'CMDB' })).toBeInTheDocument();
    const row = (await screen.findByRole('link', { name: 'erp-db-01' })).closest('tr')!;
    expect(within(row).getByRole('link', { name: 'erp-db-01' })).toHaveAttribute(
      'href',
      `/cmdb/${ci.id}`,
    );
    expect(within(row).getByText('Veritabanı')).toBeInTheDocument();
    expect(within(row).getByText('Üretim')).toBeInTheDocument();
    expect(within(row).getByText('K1 · Çok kritik')).toBeInTheDocument();
    expect(within(row).getByText('Bakımda')).toBeInTheDocument();
    expect(within(row).queryByText('dba')).not.toBeInTheDocument();
  });

  it('arama ve süzgeçler sunucuya gider', async () => {
    const calls = mockList();
    const { user } = renderWithProviders(<CmdbListPage />, { route: '/cmdb' });
    await screen.findByText('Yapılandırma öğesi bulunamadı');
    await user.type(screen.getByRole('searchbox', { name: 'Ada göre ara' }), 'erp');
    await user.click(screen.getByRole('button', { name: 'Ara' }));
    await user.selectOptions(screen.getByLabelText('Sınıf'), 'application');
    await user.selectOptions(screen.getByLabelText('Ortam'), 'test');
    await user.selectOptions(screen.getByLabelText('Durum'), 'active');
    await waitFor(() => {
      const last = calls.at(-1)!;
      expect(last.searchParams.get('q')).toBe('erp');
      expect(last.searchParams.get('ci_class')).toBe('application');
      expect(last.searchParams.get('environment')).toBe('test');
      expect(last.searchParams.get('status')).toBe('active');
    });
  });

  it('hata durumunda yeniden dene', async () => {
    server.use(
      http.get(apiUrl('/cmdb/cis'), () =>
        HttpResponse.json({ detail: 'Bu işlem için yetkiniz yok.' }, { status: 403 }),
      ),
    );
    renderWithProviders(<CmdbListPage />, { route: '/cmdb' });
    expect(await screen.findByText('Yetkiniz yok')).toBeInTheDocument();
  });
});

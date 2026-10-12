import { useQuery } from '@tanstack/react-query';
import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeIncident } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { AGENT, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { incidentsApi } from './incidents';
import { queryKeys } from './queryKeys';

function Titles() {
  const q = useQuery({
    queryKey: queryKeys.incidents.list({ mine: true }),
    queryFn: ({ signal }) => incidentsApi.listMine({}, signal),
  });
  if (q.isPending) return <p>yükleniyor</p>;
  if (q.isError) return <p>hata: {q.error.message}</p>;
  return (
    <ul>
      {q.data.map((i) => (
        <li key={i.id}>{i.title}</li>
      ))}
    </ul>
  );
}

describe('TanStack Query + API istemcisi (örnek kullanım)', () => {
  it('AbortSignal ile sorgu, oturum belirteci dahil', async () => {
    server.use(
      http.get(apiUrl('/incidents'), ({ request }) =>
        request.headers.get('authorization') === 'Bearer dev:ayse:agent'
          ? HttpResponse.json([makeIncident({ title: 'Yazıcı çalışmıyor' })])
          : HttpResponse.json({ detail: 'x' }, { status: 401 }),
      ),
    );
    renderWithProviders(<Titles />, { user: AGENT });
    expect(await screen.findByText('Yazıcı çalışmıyor')).toBeInTheDocument();
  });
});

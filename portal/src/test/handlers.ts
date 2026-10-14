/**
 * Varsayılan MSW işleyicileri. Testler kendi senaryoları için `server.use(...)` ile ezer:
 *   server.use(http.get(apiUrl('/incidents'), () => HttpResponse.json([makeIncident()])))
 */
import { http, HttpResponse } from 'msw';
import { config } from '@/config/env';

export const apiUrl = (path: string) => `${config.apiBaseUrl}${path}`;

/** `Bearer dev:<kullanıcı>:<roller>` başlığını çözer (backend dev kipiyle aynı). */
export function parseDevAuth(request: Request): { username: string; roles: string[] } | null {
  const header = request.headers.get('authorization') ?? '';
  const match = /^Bearer dev:([^:]+):(.*)$/.exec(header);
  if (!match) return null;
  return { username: match[1] ?? '', roles: (match[2] ?? '').split(',').filter(Boolean) };
}

export const unauthorized = () =>
  HttpResponse.json({ detail: 'Kimlik doğrulama gerekli' }, { status: 401 });

export const handlers = [
  http.get(apiUrl('/me'), ({ request }) => {
    const user = parseDevAuth(request);
    if (!user) return unauthorized();
    return HttpResponse.json({
      username: user.username,
      roles: [...user.roles].sort(),
      ai_confidence_threshold: 0.75,
    });
  }),
  http.get(apiUrl('/incidents'), () => HttpResponse.json([])),
  http.get(apiUrl('/incidents/decisions'), () => HttpResponse.json([])),
  http.get(apiUrl('/knowledge/articles'), () => HttpResponse.json([])),
];

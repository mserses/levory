import { expect, type APIRequestContext, type Browser, type Page } from '@playwright/test';

const ALL_ROLES = ['requester', 'agent', 'approver', 'change_manager', 'cab', 'admin'] as const;
export type E2ERole = (typeof ALL_ROLES)[number];

/** Backend adresi (Playwright `webServer` ile aynı port) */
export const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8765'}`;

/** Dev kimlik doğrulaması belirteci: `Bearer dev:<kullanıcı>:<roller>` */
export const bearer = (user: string, roles: string) => ({
  Authorization: `Bearer dev:${user}:${roles}`,
});

/** Her rol ayrı tarayıcı bağlamında (ayrı sessionStorage) dev girişiyle oturum açar. */
export async function loginAs(browser: Browser, username: string, roles: E2ERole[]): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors: string[] = [];
  pageErrors.set(page, errors);
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    // Beklenen HTTP hataları (ör. 409) tarayıcıda "Failed to load resource" olarak düşer
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) {
      errors.push(message.text());
    }
  });
  await page.goto('/giris');
  await page.getByLabel('Kullanıcı adı').fill(username);
  for (const role of ALL_ROLES) {
    const box = page.getByRole('checkbox', { name: new RegExp(`\\(${role}\\)`) });
    await box.setChecked(roles.includes(role));
  }
  await page.getByRole('button', { name: 'Giriş yap' }).click();
  await expect(page.getByRole('button', { name: 'Çıkış' })).toBeVisible();
  return page;
}

const pageErrors = new WeakMap<Page, string[]>();

/** Sayfada yakalanmamış JS hatası / konsol hatası olmadığını doğrular ve bağlamları kapatır. */
export async function closeAll(...pages: Page[]): Promise<void> {
  for (const page of pages) {
    expect(pageErrors.get(page) ?? [], `konsol hataları (${page.url()})`).toEqual([]);
    await page.context().close();
  }
}

/** Testler aynı veritabanını paylaşır: başlıklar çalıştırmaya özgü olsun */
export function unique(prefix: string): string {
  return `${prefix} ${Date.now().toString(36)}`;
}

/** Ayrıntı URL'sinden kayıt kimliği */
export function idFromUrl(page: Page): string {
  const id = new URL(page.url()).pathname.split('/').pop();
  if (!id) throw new Error(`URL'de kimlik yok: ${page.url()}`);
  return id;
}

/** Tarayıcı saat diliminde (playwright.config: Europe/Istanbul) datetime-local değeri */
export function localInput(date: Date, timeZone = 'Europe/Istanbul'): string {
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
    .format(date)
    .replace(' ', 'T');
}

/** API'den JSON gövdesiyle POST; beklenen durum kodunu doğrular */
export async function apiPost<T>(
  request: APIRequestContext,
  path: string,
  headers: Record<string, string>,
  data: unknown,
  expected = 201,
): Promise<T> {
  const response = await request.post(`${API}${path}`, { headers, data });
  expect(response.status(), `${path}: ${await response.text()}`).toBe(expected);
  return (await response.json()) as T;
}

export interface ChangeSnapshot {
  number: string;
  state: string;
  change_type: string;
  version: number;
  risk_level: string | null;
}

/** Değişikliğin sunucudaki durumu/türü (UI geçişlerinden sonra doğrulama için) */
export async function changeOf(
  request: APIRequestContext,
  id: string,
  headers: Record<string, string>,
): Promise<ChangeSnapshot> {
  const response = await request.get(`${API}/changes/${id}`, { headers });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as ChangeSnapshot;
}

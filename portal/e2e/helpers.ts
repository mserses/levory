import { expect, type Browser, type Page } from '@playwright/test';

export type E2ERole = 'requester' | 'agent' | 'approver' | 'admin';

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
  for (const role of ['requester', 'agent', 'approver', 'admin'] as const) {
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

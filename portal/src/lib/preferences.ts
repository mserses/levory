/**
 * Kullanıcı tercihleri (yalnızca tema). localStorage YALNIZCA burada kullanılır ve
 * asla belirteç/kişisel veri saklanmaz (ESLint bu dosya dışında localStorage'ı yasaklar).
 */
export type ThemePreference = 'system' | 'light' | 'dark';

const THEME_KEY = 'sz.theme';

export function readTheme(): ThemePreference {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch {
    return 'system';
  }
}

export function applyTheme(theme: ThemePreference): void {
  const root = document.documentElement;
  if (theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme);
}

export function saveTheme(theme: ThemePreference): void {
  applyTheme(theme);
  try {
    if (theme === 'system') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* tercih kaydedilemezse yalnızca bu oturumda geçerli olur */
  }
}

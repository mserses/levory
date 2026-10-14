/**
 * Girişten sonra dönülecek yol yalnızca uygulama içi olabilir (açık yönlendirme yok).
 * "//x" ve "/\x" tarayıcıda başka köken anlamına gelir; reddedilir.
 */
export function safeAppPath(value: unknown): string | null {
  return typeof value === 'string' && /^\/(?![/\\])/.test(value) ? value : null;
}

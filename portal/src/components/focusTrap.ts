/** Modal (Dialog, Drawer) odak tuzağı: Tab/Shift+Tab odağı kapsayıcının içinde tutar. */

export const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Tab tuşunu kapsayıcı içinde döndürür. Odak kapsayıcının dışındaysa (ör. odaklı buton
 * "busy" ile devre dışı kalınca tarayıcı odağı body'ye bırakır) ilk/son öğeye geri getirir;
 * aksi halde Tab arka plandaki sayfaya kaçardı.
 */
export function trapTabKey(event: KeyboardEvent, node: HTMLElement): void {
  if (event.key !== 'Tab') return;
  const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE));
  const first = items[0];
  const last = items[items.length - 1];
  if (!first || !last) {
    event.preventDefault();
    node.focus();
    return;
  }
  const active = document.activeElement;
  const inside = active instanceof Node && node.contains(active);
  if (!inside) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
    return;
  }
  if (event.shiftKey && (active === first || active === node)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

/**
 * Modal kapanınca odağı açan öğeye döndürür. O öğe bu arada DOM'dan kalktıysa (ör. form
 * yeniden kuruldu, kart listeden düştü) odak body'de kaybolmasın: ana içeriğe (#main) taşınır.
 */
export function restoreFocus(previous: HTMLElement | null): void {
  if (previous?.isConnected) {
    previous.focus();
    return;
  }
  document.getElementById('main')?.focus();
}

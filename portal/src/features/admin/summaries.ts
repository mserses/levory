/** Entegrasyon işlem sonuçlarını Türkçe bildirim metnine çevirir (saf fonksiyonlar). */
import type { ExternalSystem, SyncEvent } from '@/api/types';
import type { ToastOptions } from '@/components';
import { SYNC_STATUS_LABELS, systemLabel } from './labels';

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** POST /integrations/{system}/pull yanıtı: {fetched, created, updated, conflicts, skipped, errors} | {error} */
export function pullToast(system: ExternalSystem, result: Record<string, unknown>): ToastOptions {
  const name = systemLabel(system);
  if (typeof result.error === 'string' && result.error) {
    return { tone: 'error', title: `${name}: çekme başarısız`, description: result.error };
  }
  const conflicts = num(result.conflicts);
  const errors = num(result.errors);
  const description = [
    `${num(result.created)} yeni`,
    `${num(result.updated)} güncellendi`,
    `${conflicts} çakışma`,
    `${num(result.skipped)} atlandı`,
    `${errors} hata`,
  ].join(' · ');
  const tone = errors > 0 ? 'error' : conflicts > 0 ? 'warning' : 'success';
  return {
    tone,
    title: `${name}: ${num(result.fetched)} kayıt çekildi`,
    description:
      conflicts > 0
        ? `${description}. Çakışmalar Onaylar ekranında çözülmeyi bekliyor.`
        : description,
  };
}

export function pushToast(events: SyncEvent[]): ToastOptions {
  if (events.length === 0) {
    return {
      tone: 'info',
      title: 'Gönderilecek değişiklik yok',
      description: 'Kayıt dış sistemlerle zaten eşit.',
    };
  }
  const description = events
    .map((e) => {
      const status = SYNC_STATUS_LABELS[e.status]?.label ?? e.status;
      return `${systemLabel(e.system)}: ${status}${e.message ? ` — ${e.message}` : ''}`;
    })
    .join(' · ');
  const has = (s: string) => events.some((e) => e.status === s);
  if (has('error'))
    return { tone: 'error', title: 'Gönderim kısmen ya da tamamen başarısız', description };
  if (has('conflict')) return { tone: 'warning', title: 'Gönderimde çakışma var', description };
  return { tone: 'success', title: 'Kayıt dış sistemlere gönderildi', description };
}

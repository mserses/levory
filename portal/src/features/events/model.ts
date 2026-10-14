/** Olay yönetimi yardımcıları (backend `services/events.py`, docs/faz4.md). */
import type { EventGroup, EventSeverity } from '@/api/types';
import type { Tone } from '@/components';

export const SEVERITY_TONES: Record<EventSeverity, Tone> = {
  critical: 'danger',
  high: 'warning',
  warning: 'info',
  info: 'neutral',
};

/** `event.silence` süre seçenekleri (dk; backend 5 – 10080) */
export const SILENCE_OPTIONS = [
  { value: '30', label: '30 dakika' },
  { value: '60', label: '1 saat' },
  { value: '240', label: '4 saat' },
  { value: '1440', label: '1 gün' },
  { value: '10080', label: '7 gün' },
] as const;

export function silenceLabel(minutes: number): string {
  return (
    SILENCE_OPTIONS.find((o) => o.value === String(minutes))?.label ?? `${String(minutes)} dakika`
  );
}

export function isSilenced(group: EventGroup, now: Date = new Date()): boolean {
  return group.silenced_until !== null && new Date(group.silenced_until).getTime() > now.getTime();
}

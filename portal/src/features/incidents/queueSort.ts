import type { Incident } from '@/api/types';
import { computeSla } from '@/lib/sla';

/**
 * SLA vadesine göre: ihlal/en yakın vade önce; duraklatılmış saatler işleyenlerden sonra
 * (vadesine göre); SLA'sı olmayan/karşılanmış kayıtlar en sonda. Kararlı sıralama.
 */
export function sortBySlaDue(incidents: readonly Incident[], now: Date): Incident[] {
  const key = (i: Incident): number => {
    const sla = computeSla(i, now);
    if (!sla.dueAt || sla.state === 'none' || sla.state === 'met' || sla.state === 'missed') {
      return Number.POSITIVE_INFINITY;
    }
    return sla.state === 'paused' ? 1e15 + sla.dueAt.getTime() : sla.dueAt.getTime();
  };
  return incidents
    .map((incident, index) => ({ incident, index, k: key(incident) }))
    .sort((a, b) => (a.k === b.k ? a.index - b.index : a.k < b.k ? -1 : 1))
    .map((x) => x.incident);
}

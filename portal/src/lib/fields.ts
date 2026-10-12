/**
 * Olay alanlarının Türkçe adları ve değerlerinin gösterimi. Yapay zekâ önerisi, onay kutusu,
 * senkron çakışmaları ve kayıt ayrıntısı aynı fonksiyonları kullanır (ham anahtar gösterilmez).
 */
import {
  IMPACT_LABELS,
  KIND_LABELS,
  PRIORITY_LABELS,
  STATUS_LABELS,
  URGENCY_LABELS,
} from './labels';
import { isTaxonomyField, taxonomyLabel } from './taxonomy';

export const FIELD_LABELS: Readonly<Record<string, string>> = {
  kind: 'Tür',
  category: 'Kategori',
  service: 'Hizmet',
  impact: 'Etki',
  urgency: 'Aciliyet',
  assignment_group: 'Atama grubu',
  priority: 'Öncelik',
  title: 'Başlık',
  description: 'Açıklama',
  status: 'Durum',
  assignee: 'Atanan',
  resolution: 'Çözüm',
};

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

function lookup(map: Record<string, string>, value: string): string {
  return Object.prototype.hasOwnProperty.call(map, value) ? (map[value] ?? value) : value;
}

/** Alan değerinin Türkçe gösterimi: enum/taksonomi ise ad, değilse düz metin; boşsa "—". */
export function displayValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  if (isTaxonomyField(field)) return taxonomyLabel(field, text);
  switch (field) {
    case 'kind':
      return lookup(KIND_LABELS, text);
    case 'impact':
      return lookup(IMPACT_LABELS, text);
    case 'urgency':
      return lookup(URGENCY_LABELS, text);
    case 'priority':
      return lookup(PRIORITY_LABELS, text);
    case 'status':
      return lookup(STATUS_LABELS, text);
    default:
      return text;
  }
}

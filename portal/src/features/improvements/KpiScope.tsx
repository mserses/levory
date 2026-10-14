import type { Improvement, KPIDefinition } from '@/api/types';
import { isPriority } from '@/features/kpi/model';
import { CHANGE_TYPE_LABELS, KPI_DIMENSION_LABELS, PRIORITY_LABELS } from '@/lib/labels';
import { categoryLabel, groupLabel, serviceLabel } from '@/lib/taxonomy';

function dimensionValue(dimension: string, value: string): string {
  if (dimension === 'priority' && isPriority(value)) return PRIORITY_LABELS[value];
  if (dimension === 'assignment_group') return groupLabel(value);
  if (dimension === 'category') return categoryLabel(value);
  if (dimension === 'service') return serviceLabel(value);
  if (dimension === 'change_type' && value in CHANGE_TYPE_LABELS) {
    return CHANGE_TYPE_LABELS[value as keyof typeof CHANGE_TYPE_LABELS];
  }
  return value;
}

/** "Çözüm grubu: Ağ operasyonları" — ölçülen KPI kırılımı */
export function scopeText(imp: Improvement): string | null {
  if (!imp.kpi_dimension || !imp.kpi_dimension_value) return null;
  const dim = KPI_DIMENSION_LABELS[imp.kpi_dimension] ?? imp.kpi_dimension;
  return `${dim}: ${dimensionValue(imp.kpi_dimension, imp.kpi_dimension_value)}`;
}

export function kpiName(imp: Improvement, defs: readonly KPIDefinition[] | undefined): string {
  if (!imp.kpi_key) return 'KPI bağlı değil';
  return defs?.find((d) => d.key === imp.kpi_key)?.name ?? imp.kpi_key;
}

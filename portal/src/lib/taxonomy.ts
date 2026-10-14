/**
 * Sınıflandırma taksonomisi (kategori, hizmet, atama grubu).
 *
 * Tek kaynak backend `src/serviszeka/taxonomy.json`'dur; `npm run gen:taxonomy` (ve `sync:api`)
 * onu `src/lib/taxonomy.json` olarak kopyalar, CI kopyanın güncel olduğunu denetler.
 * Elle düzenlemeyin. Ekranlar ham anahtar yerine buradaki Türkçe adları gösterir.
 */
import taxonomy from './taxonomy.json';

export type TaxonomyField = 'category' | 'service' | 'assignment_group';

/** "Ağ ve bağlantı (VPN, Wi-Fi, …)" → "Ağ ve bağlantı" (kısa ad; tablolar ve rozetler için) */
function shortName(description: string): string {
  const cut = description.split(/\s[(:]/)[0] ?? description;
  return cut.trim() || description;
}

/**
 * Kısa Türkçe adlar (tablolar, rozetler, seçim listeleri). Anahtarlar taksonomiden gelir;
 * burada adı olmayan yeni bir anahtar açıklamasının ilk parçasıyla gösterilir
 * (taxonomy.test.ts her anahtarın kısa adı olduğunu denetler).
 */
const CATEGORY_SHORT: Record<string, string> = {
  ag: 'Ağ ve bağlantı',
  donanim: 'Donanım',
  yazilim: 'Yazılım ve uygulamalar',
  erisim_kimlik: 'Hesap, parola ve yetki',
  eposta_isbirligi: 'E-posta ve iş birliği',
  guvenlik: 'Bilgi güvenliği',
  is_uygulamasi: 'Kurumsal iş uygulamaları',
  veritabani: 'Veritabanı',
  altyapi_bulut: 'Altyapı ve bulut',
  yazici: 'Yazıcı ve tarayıcı',
  mobil: 'Mobil cihazlar',
};

const GROUP_SHORT: Record<string, string> = {
  service_desk_l1: '1. seviye hizmet masası',
  end_user_support: 'Masaüstü/saha destek',
  network_ops: 'Ağ operasyonları',
  identity_access: 'Kimlik ve erişim yönetimi',
  collaboration: 'E-posta ve iş birliği ekibi',
  security_ops: 'Güvenlik operasyon merkezi (SOC)',
  business_apps: 'İş uygulamaları desteği',
  dba: 'Veritabanı yöneticileri',
  infra_cloud: 'Sunucu, depolama ve bulut',
  mobility: 'Mobil cihaz ekibi',
};

function withShort(
  map: Record<string, string>,
  short: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(Object.entries(map).map(([k, v]) => [k, short[k] ?? shortName(v)]));
}

/** Kategori kısa adları (ör. ag → "Ağ ve bağlantı") */
export const CATEGORY_LABELS: Readonly<Record<string, string>> = withShort(
  taxonomy.category,
  CATEGORY_SHORT,
);
/** Hizmet adları (tam ad; hizmet adları zaten kısadır) */
export const SERVICE_LABELS: Readonly<Record<string, string>> = taxonomy.service;
/** Atama grubu kısa adları (ör. network_ops → "Ağ operasyonları") */
export const GROUP_LABELS: Readonly<Record<string, string>> = withShort(
  taxonomy.assignment_group,
  GROUP_SHORT,
);

/** Kısa adı eksik taksonomi anahtarları (testte boş olmalı) */
export function missingShortNames(): string[] {
  return [
    ...Object.keys(taxonomy.category).filter((k) => !(k in CATEGORY_SHORT)),
    ...Object.keys(taxonomy.assignment_group).filter((k) => !(k in GROUP_SHORT)),
  ];
}
/** Uzun açıklamalar (seçim listelerinde yardım metni olarak) */
export const TAXONOMY_DESCRIPTIONS: Readonly<Record<TaxonomyField, Record<string, string>>> = {
  category: taxonomy.category,
  service: taxonomy.service,
  assignment_group: taxonomy.assignment_group,
};

const LABELS: Record<TaxonomyField, Readonly<Record<string, string>>> = {
  category: CATEGORY_LABELS,
  service: SERVICE_LABELS,
  assignment_group: GROUP_LABELS,
};

export function isTaxonomyField(field: string): field is TaxonomyField {
  return field === 'category' || field === 'service' || field === 'assignment_group';
}

export function isTaxonomyValue(field: TaxonomyField, value: string): boolean {
  return Object.prototype.hasOwnProperty.call(LABELS[field], value);
}

/** Taksonomi değerinin Türkçe adı; listede olmayan değer "Diğer (değer)" olarak gösterilir. */
export function taxonomyLabel(field: TaxonomyField, value: string): string {
  return isTaxonomyValue(field, value) ? (LABELS[field][value] ?? value) : `Diğer (${value})`;
}

export const categoryLabel = (value: string) => taxonomyLabel('category', value);
export const serviceLabel = (value: string) => taxonomyLabel('service', value);
export const groupLabel = (value: string) => taxonomyLabel('assignment_group', value);

export interface TaxonomyOption {
  value: string;
  label: string;
}

/**
 * Seçim listesi seçenekleri (Türkçe ada göre sıralı). `current` taksonomi dışıysa listeye
 * eklenir ki mevcut kayıt düzenlenirken değeri kaybolmasın.
 */
export function taxonomyOptions(field: TaxonomyField, current?: string | null): TaxonomyOption[] {
  const options = Object.entries(LABELS[field])
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label, 'tr'));
  if (current && !isTaxonomyValue(field, current)) {
    options.push({ value: current, label: taxonomyLabel(field, current) });
  }
  return options;
}

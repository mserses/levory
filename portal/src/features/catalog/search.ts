/** Türkçe karakter ve büyük/küçük harf duyarsız arama anahtarı ("Şİfre" → "sifre") */
const FOLD: Record<string, string> = { ı: 'i', ş: 's', ğ: 'g', ç: 'c', ö: 'o', ü: 'u' };

export function foldText(value: string): string {
  return value
    .toLocaleLowerCase('tr-TR')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[ışğçöü]/g, (ch) => FOLD[ch] ?? ch)
    .trim();
}

/** Tüm sözcükler metinlerden birinde geçiyorsa eşleşir */
export function matchesQuery(query: string, ...texts: readonly string[]): boolean {
  const words = foldText(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const haystack = foldText(texts.join(' '));
  return words.every((w) => haystack.includes(w));
}

/**
 * Arama parçacığı vurgulama — HTML üretmeden, metni parçalara böler.
 * Backend araması gibi Türkçe büyük/küçük harf duyarsız (I→ı, İ→i) ve kök önekiyle eşleşir:
 * "sertifika" sorgusu "Sertifikası" kelimesinin tamamını vurgular.
 */

export interface Segment {
  text: string;
  match: boolean;
}

/** Backend PREFIX_LEN ile aynı: uzun terimler ilk 5 harfle (kök) eşleşir */
const PREFIX_LEN = 5;
const MIN_TOKEN = 2;
const STOPWORDS = new Set([
  've',
  'veya',
  'ile',
  'için',
  'bir',
  'bu',
  'şu',
  'da',
  'de',
  'mi',
  'mı',
  'mu',
  'mü',
  'ne',
  'nasıl',
  'gibi',
  'the',
  'and',
  'or',
  'of',
  'to',
  'in',
  'is',
  'for',
  'on',
]);

const WORD_CHAR = /[\p{L}\p{N}]/u;

/** Karakter başına küçük harf: uzunluk korunur, böylece katlanmış metindeki indeksler asıl metne denk gelir */
function foldChar(ch: string): string {
  const lower = ch.toLocaleLowerCase('tr');
  return lower.length === ch.length ? lower : ch;
}

export function foldTurkish(text: string): string {
  let out = '';
  for (let i = 0; i < text.length; i++) out += foldChar(text.charAt(i));
  return out;
}

export function queryNeedles(query: string): string[] {
  const tokens = foldTurkish(query)
    .split(/[^\p{L}\p{N}]+/u)
    .filter((t) => t.length >= MIN_TOKEN && !STOPWORDS.has(t))
    .map((t) => (t.length > PREFIX_LEN ? t.slice(0, PREFIX_LEN) : t));
  return [...new Set(tokens)];
}

const isWordChar = (ch: string | undefined) => ch !== undefined && ch !== '' && WORD_CHAR.test(ch);

export function highlightSegments(text: string, query: string): Segment[] {
  const needles = queryNeedles(query);
  if (!text || needles.length === 0) return text ? [{ text, match: false }] : [];
  const folded = foldTurkish(text);
  const ranges: [number, number][] = [];
  for (const needle of needles) {
    let from = 0;
    for (;;) {
      const start = folded.indexOf(needle, from);
      if (start === -1) break;
      from = start + 1;
      if (isWordChar(folded[start - 1])) continue; // yalnızca kelime başı
      let end = start + needle.length;
      while (end < folded.length && isWordChar(folded[end])) end++;
      ranges.push([start, end]);
    }
  }
  if (ranges.length === 0) return [{ text, match: false }];
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const r of ranges) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([r[0], r[1]]);
  }
  const segments: Segment[] = [];
  let pos = 0;
  for (const [s, e] of merged) {
    if (s > pos) segments.push({ text: text.slice(pos, s), match: false });
    segments.push({ text: text.slice(s, e), match: true });
    pos = e;
  }
  if (pos < text.length) segments.push({ text: text.slice(pos), match: false });
  return segments;
}

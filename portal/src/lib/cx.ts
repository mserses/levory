/** Koşullu sınıf adı birleştirici: cx('a', cond && 'b') */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

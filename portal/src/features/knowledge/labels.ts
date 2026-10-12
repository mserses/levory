/** Bilgi bankası etiketleri. Kategori anahtarları backend `taxonomy.json` ile aynıdır. */
import type { Tone } from '@/components';
import { taxonomyLabel } from '@/lib/taxonomy';

export type ArticleState = 'draft' | 'published' | 'retired';

export const ARTICLE_STATES: readonly ArticleState[] = ['draft', 'published', 'retired'];

export const ARTICLE_STATE_LABELS: Record<ArticleState, string> = {
  draft: 'Taslak',
  published: 'Yayında',
  retired: 'Arşivde',
};

export const ARTICLE_STATE_TONES: Record<ArticleState, Tone> = {
  draft: 'warning',
  published: 'success',
  retired: 'neutral',
};

// Kategori adları ortak taksonomiden (backend taxonomy.json → @/lib/taxonomy)
export { CATEGORY_LABELS } from '@/lib/taxonomy';

export function isArticleState(value: string): value is ArticleState {
  return (ARTICLE_STATES as readonly string[]).includes(value);
}

/** Ham anahtar ekranda gösterilmez; bilinmeyen kategori "Diğer (…)" olarak etiketlenir. */
export function categoryLabel(category: string | null | undefined): string {
  if (!category) return 'Kategorisiz';
  return taxonomyLabel('category', category);
}

export function stateLabel(state: string): string {
  return isArticleState(state) ? ARTICLE_STATE_LABELS[state] : state;
}

export function stateTone(state: string): Tone {
  return isArticleState(state) ? ARTICLE_STATE_TONES[state] : 'neutral';
}

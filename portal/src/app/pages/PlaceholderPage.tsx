import type { ReactNode } from 'react';
import { EmptyState } from '@/components/EmptyState';
import { usePageTitle } from '@/lib/usePageTitle';

/** Geçici ekran iskeleti: özellik ajanları kendi sayfalarında bunu kaldırır. */
export function PlaceholderPage({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: ReactNode;
}) {
  usePageTitle(title);
  return (
    <div className="page">
      <div className="page-header">
        <h1>{title}</h1>
      </div>
      {children ?? <EmptyState icon="🛠" title="Bu ekran hazırlanıyor" description={description} />}
    </div>
  );
}

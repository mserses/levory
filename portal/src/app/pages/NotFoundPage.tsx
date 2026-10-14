import { Link } from 'react-router-dom';
import { EmptyState } from '@/components/EmptyState';

export function NotFoundPage() {
  return (
    <EmptyState
      icon="?"
      title="Sayfa bulunamadı"
      description="Aradığınız sayfa taşınmış ya da hiç var olmamış olabilir."
      action={<Link to="/">Ana sayfaya dön</Link>}
    />
  );
}

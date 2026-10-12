import { Link } from 'react-router-dom';
import { EmptyState } from '@/components/EmptyState';

export function ForbiddenPage() {
  return (
    <EmptyState
      icon="⛔"
      title="Bu sayfayı görüntüleme yetkiniz yok"
      description="Rolünüz bu ekrana erişim için yeterli değil. Erişim gerekiyorsa sistem yöneticinize başvurun."
      action={<Link to="/">Ana sayfaya dön</Link>}
    />
  );
}

import { Navigate } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { homePathFor } from '../navigation';

/** "/" -> role göre açılış sayfası (RequireRole içinde kullanılır) */
export function HomeRedirect() {
  const { roles } = useAuth();
  return <Navigate to={homePathFor(roles)} replace />;
}
